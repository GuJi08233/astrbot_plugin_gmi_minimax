"""GMI Cloud requestqueue API client for MiniMax audio models.

All GMI-hosted MiniMax models share one request queue API:
submit ``POST /api/v1/ie/requestqueue/apikey/requests`` with
``{"model": ..., "payload": ...}`` then poll
``GET /api/v1/ie/requestqueue/apikey/requests/{request_id}`` until the
status becomes ``success`` / ``failed`` / ``cancelled``. Results are
delivered as URLs in ``outcome.media_urls``.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path

import aiohttp

DEFAULT_API_BASE = "https://console.gmicloud.ai"
REQUESTS_PATH = "/api/v1/ie/requestqueue/apikey/requests"
MODELS_PATH = "/api/v1/ie/requestqueue/apikey/models"

TERMINAL_FAILURE_STATUSES = {"failed", "cancelled"}

MUSIC_LYRICS_MAX_CHARS = 3500
MUSIC_PROMPT_MAX_CHARS = 2000


class GMIError(Exception):
    """Raised when the GMI API returns an error or an invalid response.

    ``transient`` marks failures worth retrying (network hiccups, DNS
    resolution, HTTP 5xx); parameter/content errors stay non-transient.
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


def build_music_payload(
    lyrics: str,
    prompt: str = "",
    *,
    sample_rate: int = 44100,
    bitrate: int = 256000,
    audio_format: str = "mp3",
) -> dict:
    lyrics = str(lyrics or "").strip()
    if not lyrics:
        raise GMIError("歌词不能为空")
    if len(lyrics) > MUSIC_LYRICS_MAX_CHARS:
        raise GMIError(f"歌词超过 {MUSIC_LYRICS_MAX_CHARS} 字符上限")
    prompt = str(prompt or "").strip()
    if len(prompt) > MUSIC_PROMPT_MAX_CHARS:
        raise GMIError(f"风格描述超过 {MUSIC_PROMPT_MAX_CHARS} 字符上限")

    payload: dict = {
        "lyrics": lyrics,
        "sample_rate": int(sample_rate),
        "bitrate": int(bitrate),
        "format": audio_format,
    }
    if prompt:
        payload["prompt"] = prompt
    return payload


def build_tts_payload(
    text: str,
    *,
    voice_id: str = "",
    speed: float = 1.0,
    vol: float = 1.0,
    pitch: int = 0,
    emotion: str = "auto",
    sound_effects: str = "",
    audio_format: str = "mp3",
    sample_rate: int = 32000,
    bitrate: int = 128000,
    channel: int = 2,
) -> dict:
    text = str(text or "").strip()
    if not text:
        raise GMIError("合成文本不能为空")

    # GMI 的 TTS 示例中这些字段以字符串形式提交。
    payload: dict = {
        "text": text,
        "speed": str(speed),
        "vol": str(vol),
        "pitch": str(int(pitch)),
        "emotion": emotion or "auto",
        "language_boost": "auto",
        "format": audio_format,
        "audio_sample_rate": str(int(sample_rate)),
        "bitrate": str(int(bitrate)),
        "channel": str(int(channel)),
    }
    if voice_id:
        payload["voice_id"] = voice_id
    if sound_effects:
        payload["sound_effects"] = sound_effects
    return payload


def build_voice_clone_payload(
    text: str,
    source_audio: str,
    *,
    voice_id: str = "",
    prompt_audio: str = "",
    prompt_text: str = "",
    need_noise_reduction: bool = True,
    need_volume_normalization: bool = True,
) -> dict:
    text = str(text or "").strip()
    if not text:
        raise GMIError("合成文本不能为空")
    source_audio = str(source_audio or "").strip()
    if not source_audio.lower().startswith(("http://", "https://")):
        raise GMIError("音色克隆需要参考音频的 HTTP/HTTPS URL (source_audio)")
    # 文档称 voice_id 可选，实测接口必填：须字母开头、8-256 位且不可重复。
    voice_id = str(voice_id or "").strip() or f"astrbot_{uuid.uuid4().hex[:24]}"

    payload: dict = {
        "text": text,
        "source_audio": source_audio,
        "voice_id": voice_id,
        # GMI 接口字段拼写即为 volumn。
        "need_noise_reduction": bool(need_noise_reduction),
        "need_volumn_normalization": bool(need_volume_normalization),
    }
    prompt_audio = str(prompt_audio or "").strip()
    prompt_text = str(prompt_text or "").strip()
    if prompt_audio:
        if not prompt_text:
            raise GMIError("配置了 prompt_audio 时必须同时配置 prompt_text")
        payload["prompt_audio"] = prompt_audio
        payload["prompt_text"] = prompt_text
    return payload


_FILENAME_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t ]+')
_STRUCTURE_TAG_LINE = re.compile(r"^[\[(（【][^\]）】)]{1,20}[\])）】]$")


def build_music_filename(title: str, lyrics: str, job_id: str, ext: str) -> str:
    """Build a recognizable music filename like ``歌名_ab12cd.mp3``.

    Falls back to the first non-structure-tag lyric line when no title
    is given, and to "song" when both are empty.
    """
    base = str(title or "").strip()
    if not base:
        for line in str(lyrics or "").splitlines():
            line = line.strip()
            if line and not _STRUCTURE_TAG_LINE.match(line):
                base = line
                break
    base = _FILENAME_UNSAFE.sub("_", base).strip("_.")[:24].strip("_.") or "song"
    suffix = str(ext or "mp3").lstrip(".")
    return f"{base}_{job_id}.{suffix}"


def extract_media_urls(detail: dict) -> list[str]:
    """Pull media URLs from a finished request detail."""
    outcome = detail.get("outcome") or {}
    if not isinstance(outcome, dict):
        return []
    urls: list[str] = []
    for item in outcome.get("media_urls") or []:
        if isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]))
    if not urls:
        for item in outcome.get("medias") or []:
            if isinstance(item, dict) and item.get("url"):
                urls.append(str(item["url"]))
    if not urls and outcome.get("audio_url"):
        urls.append(str(outcome["audio_url"]))
    return urls


def describe_failure(detail: dict) -> str:
    """Build a short failure reason from a failed request detail."""
    outcome = detail.get("outcome")
    if isinstance(outcome, dict) and outcome:
        for key in ("error", "message", "status"):
            value = outcome.get(key)
            if value:
                return str(value)[:300]
        return json.dumps(outcome, ensure_ascii=False)[:300]
    return str(detail.get("status") or "unknown")


class GMIClient:
    def __init__(
        self,
        api_key: str,
        *,
        api_base: str = DEFAULT_API_BASE,
        request_timeout: float = 180.0,
        poll_interval: float = 3.0,
        poll_timeout: float = 300.0,
        proxy: str | None = None,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.api_base = (str(api_base or "").strip() or DEFAULT_API_BASE).rstrip("/")
        self.request_timeout = max(10.0, float(request_timeout or 180))
        self.poll_interval = max(1.0, float(poll_interval or 3))
        self.poll_timeout = max(10.0, float(poll_timeout or 300))
        self.proxy = str(proxy or "").strip() or None

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _request_json(
        self, method: str, path: str, json_body: dict | None = None
    ) -> dict:
        if not self.api_key:
            raise GMIError("未配置 GMI API Key")
        url = f"{self.api_base}{path}"
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    url,
                    json=json_body,
                    headers=self._headers(),
                    proxy=self.proxy,
                ) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        raise GMIError(
                            f"GMI API HTTP {resp.status}: {body[:300]}",
                            transient=resp.status >= 500,
                        )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise GMIError(f"GMI 网络请求失败: {exc}", transient=True) from exc
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise GMIError(f"GMI API 返回非 JSON 响应: {body[:200]}") from exc
        if not isinstance(data, dict):
            raise GMIError("GMI API 返回格式异常")
        return data

    async def submit(self, model: str, payload: dict) -> dict:
        return await self._request_json(
            "POST", REQUESTS_PATH, {"model": model, "payload": payload}
        )

    async def get_request(self, request_id: str) -> dict:
        return await self._request_json("GET", f"{REQUESTS_PATH}/{request_id}")

    async def list_models(self) -> list[str]:
        data = await self._request_json("GET", MODELS_PATH)
        model_ids = data.get("model_ids")
        return [str(m) for m in model_ids] if isinstance(model_ids, list) else []

    async def generate(self, model: str, payload: dict, *, retries: int = 0) -> dict:
        """Submit a request, wait until it finishes, retry transient failures.

        GMI occasionally fails whole generations with "unknown error ...
        Please try again" during load spikes; such failures are retried up
        to ``retries`` times. Other failures raise immediately.

        Returns:
            The final request detail whose ``outcome`` carries media URLs.
        """
        for attempt in range(retries + 1):
            try:
                return await self._generate_once(model, payload)
            except GMIError as exc:
                transient = exc.transient or "try again" in str(exc).lower()
                if attempt < retries and transient:
                    await asyncio.sleep(self.poll_interval * 2)
                    continue
                raise
        raise GMIError("GMI 任务重试次数耗尽")

    async def _generate_once(self, model: str, payload: dict) -> dict:
        detail = await self.submit(model, payload)
        request_id = str(detail.get("request_id") or "")
        if not request_id:
            raise GMIError(f"GMI 未返回 request_id: {json.dumps(detail)[:200]}")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.poll_timeout
        status = str(detail.get("status") or "")

        while True:
            if status in TERMINAL_FAILURE_STATUSES:
                raise GMIError(f"GMI 任务{status}: {describe_failure(detail)}")
            if status == "success":
                # 提交响应通常不含 outcome，success 后仍需查询一次详情。
                if extract_media_urls(detail):
                    return detail
                detail = await self.get_request(request_id)
                if extract_media_urls(detail):
                    return detail
                raise GMIError(
                    "GMI 任务成功但未返回媒体链接: " + describe_failure(detail)
                )
            if loop.time() >= deadline:
                raise GMIError(
                    f"GMI 任务等待超时 ({int(self.poll_timeout)}s)，"
                    f"request_id={request_id}"
                )
            await asyncio.sleep(self.poll_interval)
            detail = await self.get_request(request_id)
            status = str(detail.get("status") or "")

    async def download(self, url: str, dest: Path) -> Path:
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, proxy=self.proxy) as resp:
                    if resp.status != 200:
                        raise GMIError(
                            f"下载音频失败: HTTP {resp.status}",
                            transient=resp.status >= 500,
                        )
                    data = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise GMIError(f"下载音频失败: {exc}", transient=True) from exc
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        # 媒体文件可能由协议端进程（另一容器）读取发送，显式放宽权限。
        try:
            dest.chmod(0o644)
        except OSError:
            pass
        return dest

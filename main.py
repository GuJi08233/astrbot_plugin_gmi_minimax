"""GMI MiniMax 音频插件 — Music 3.0 音乐生成与 Speech 2.8 语音合成/音色克隆。"""

import re
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger, star
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.core.message.components import Record
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .gmi_client import (
    GMIClient,
    GMIError,
    build_music_payload,
    build_tts_payload,
    build_voice_clone_payload,
    extract_media_urls,
)

PLUGIN_NAME = "astrbot_plugin_gmi_minimax"
MUSIC_MODEL = "minimax-music-3.0"
URL_PATTERN = re.compile(r"^https?://\S+$", re.IGNORECASE)


@filter.command_group("gmi")
def gmi_group() -> None:
    """GMI MiniMax 音频指令组"""


class Main(star.Star):
    """GMI Cloud MiniMax audio plugin — music generation, TTS and voice clone."""

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self._config = config
        self._data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._client = self._build_client()

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #

    def _build_client(self) -> GMIClient:
        cfg = self._config
        client = GMIClient(
            api_key=str(cfg.get("api_key", "")),
            api_base=str(cfg.get("api_base", "")),
            request_timeout=self._get_float("request_timeout", 180),
            poll_interval=self._get_float("poll_interval", 3),
            poll_timeout=self._get_float("poll_timeout", 300),
            proxy=str(cfg.get("proxy", "")),
        )
        if not client.api_key:
            logger.warning(f"[{PLUGIN_NAME}] api_key 未配置，插件无法调用 GMI API")
        return client

    def _get_float(self, key: str, default: float) -> float:
        try:
            return float(self._config.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    def _get_int(self, section: dict, key: str, default: int) -> int:
        try:
            return int(section.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    def _section(self, name: str) -> dict:
        value = self._config.get(name, {})
        return value if isinstance(value, dict) else {}

    # ------------------------------------------------------------------ #
    # 生成与发送
    # ------------------------------------------------------------------ #

    async def _generate_music(self, lyrics: str, prompt: str) -> Path:
        cfg = self._section("music")
        audio_format = str(cfg.get("format", "mp3") or "mp3")
        payload = build_music_payload(
            lyrics,
            prompt,
            sample_rate=self._get_int(cfg, "sample_rate", 44100),
            bitrate=self._get_int(cfg, "bitrate", 256000),
            audio_format=audio_format,
        )
        detail = await self._client.generate(MUSIC_MODEL, payload)
        return await self._download_first_media(detail, "music", audio_format)

    async def _generate_tts(self, text: str, emotion: str = "") -> Path:
        cfg = self._section("tts")
        audio_format = str(cfg.get("format", "mp3") or "mp3")
        payload = build_tts_payload(
            text,
            voice_id=str(cfg.get("voice_id", "") or ""),
            speed=float(cfg.get("speed", 1.0) or 1.0),
            vol=float(cfg.get("vol", 1.0) or 1.0),
            pitch=self._get_int(cfg, "pitch", 0),
            emotion=(emotion or str(cfg.get("emotion", "auto") or "auto")),
            sound_effects=str(cfg.get("sound_effects", "") or ""),
            audio_format=audio_format,
            sample_rate=self._get_int(cfg, "sample_rate", 32000),
            bitrate=self._get_int(cfg, "bitrate", 128000),
            channel=self._get_int(cfg, "channel", 2),
        )
        model = str(
            cfg.get("model", "minimax-tts-speech-2.8-turbo")
            or "minimax-tts-speech-2.8-turbo"
        )
        detail = await self._client.generate(model, payload)
        return await self._download_first_media(detail, "tts", audio_format)

    async def _generate_voice_clone(self, text: str, source_audio: str = "") -> Path:
        cfg = self._section("voice_clone")
        source = source_audio or str(cfg.get("source_audio", "") or "")
        payload = build_voice_clone_payload(
            text,
            source,
            prompt_audio=str(cfg.get("prompt_audio", "") or ""),
            prompt_text=str(cfg.get("prompt_text", "") or ""),
            need_noise_reduction=bool(cfg.get("need_noise_reduction", True)),
            need_volume_normalization=bool(cfg.get("need_volume_normalization", True)),
        )
        model = str(
            cfg.get("model", "minimax-audio-voice-clone-speech-2.8-turbo")
            or "minimax-audio-voice-clone-speech-2.8-turbo"
        )
        detail = await self._client.generate(model, payload)
        return await self._download_first_media(detail, "clone", "mp3")

    async def _download_first_media(
        self, detail: dict, prefix: str, audio_format: str
    ) -> Path:
        urls = extract_media_urls(detail)
        if not urls:
            raise GMIError("GMI 未返回音频链接")
        dest = self._data_dir / f"gmi_{prefix}_{int(time.time() * 1000)}.{audio_format}"
        return await self._client.download(urls[0], dest)

    async def _send_record(self, event: AstrMessageEvent, path: Path) -> None:
        await event.send(MessageChain(chain=[Record(file=str(path))]))

    # ------------------------------------------------------------------ #
    # 指令
    # ------------------------------------------------------------------ #

    @staticmethod
    def _strip_command(event: AstrMessageEvent, sub_command: str) -> str:
        """Return the raw text after '/gmi <sub_command>'."""
        text = event.message_str.strip()
        parts = text.split(maxsplit=2)
        if len(parts) >= 3 and parts[0] == "gmi" and parts[1] == sub_command:
            return parts[2]
        return ""

    @gmi_group.command("music")
    async def cmd_music(self, event: AstrMessageEvent):
        """生成音乐。用法: /gmi music <风格描述>，从第二行起为歌词；单行输入时整体视为歌词"""
        raw = self._strip_command(event, "music")
        if not raw.strip():
            yield event.plain_result(
                "用法: /gmi music <风格描述>\n<歌词，可用 [Verse]/[Chorus] 等标签>\n"
                "只写一行时整体作为歌词。"
            )
            return
        first_line, _, rest = raw.partition("\n")
        if rest.strip():
            prompt, lyrics = first_line.strip(), rest.strip()
        else:
            prompt, lyrics = "", first_line.strip()

        yield event.plain_result("🎵 正在生成音乐，通常需要 30-60 秒…")
        try:
            path = await self._generate_music(lyrics, prompt)
        except GMIError as e:
            yield event.plain_result(f"❌ 音乐生成失败: {e}")
            return
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 音乐生成异常: {e}", exc_info=True)
            yield event.plain_result(f"❌ 音乐生成异常: {e}")
            return
        yield event.chain_result([Record(file=str(path))])

    @gmi_group.command("speech")
    async def cmd_speech(self, event: AstrMessageEvent):
        """语音合成。用法: /gmi speech <文本>"""
        text = self._strip_command(event, "speech").strip()
        if not text:
            yield event.plain_result("用法: /gmi speech <要合成的文本>")
            return
        try:
            path = await self._generate_tts(text)
        except GMIError as e:
            yield event.plain_result(f"❌ 语音合成失败: {e}")
            return
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 语音合成异常: {e}", exc_info=True)
            yield event.plain_result(f"❌ 语音合成异常: {e}")
            return
        yield event.chain_result([Record(file=str(path))])

    @gmi_group.command("clone")
    async def cmd_clone(self, event: AstrMessageEvent):
        """音色克隆合成。用法: /gmi clone [参考音频URL] <文本>"""
        raw = self._strip_command(event, "clone").strip()
        if not raw:
            yield event.plain_result(
                "用法: /gmi clone <文本>（使用配置的参考音频）\n"
                "或: /gmi clone <参考音频URL> <文本>"
            )
            return
        source_audio = ""
        text = raw
        first, _, rest = raw.partition(" ")
        if URL_PATTERN.match(first) and rest.strip():
            source_audio, text = first, rest.strip()

        try:
            path = await self._generate_voice_clone(text, source_audio)
        except GMIError as e:
            yield event.plain_result(f"❌ 音色克隆失败: {e}")
            return
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 音色克隆异常: {e}", exc_info=True)
            yield event.plain_result(f"❌ 音色克隆异常: {e}")
            return
        yield event.chain_result([Record(file=str(path))])

    @gmi_group.command("models")
    async def cmd_models(self, event: AstrMessageEvent):
        """查询 GMI 可用模型（验证 Key 连通）"""
        try:
            models = await self._client.list_models()
        except GMIError as e:
            yield event.plain_result(f"❌ 查询失败: {e}")
            return
        if not models:
            yield event.plain_result("查询成功，但模型列表为空。")
            return
        yield event.plain_result(
            "GMI 可用模型:\n" + "\n".join(f"- {m}" for m in models)
        )

    # ------------------------------------------------------------------ #
    # LLM 工具
    # ------------------------------------------------------------------ #

    @filter.llm_tool(name="gmi_generate_music")
    async def tool_generate_music(
        self, event: AstrMessageEvent, lyrics: str, prompt: str = ""
    ):
        """用 MiniMax Music 3.0 生成一首完整歌曲并直接发送给用户。当用户想要创作、生成音乐或歌曲时调用；你可以先根据用户需求写好歌词再调用本工具。生成通常需要 30-60 秒。

        Args:
            lyrics(string): 完整歌词，用换行分隔诗句，可包含 [Verse]、[Chorus]、[Bridge]、[Intro]、[Outro] 等结构标签，1-3500 字符
            prompt(string): 音乐风格描述（曲风、情绪、乐器、节奏、人声类型等），可留空
        """
        try:
            path = await self._generate_music(lyrics, prompt)
            await self._send_record(event, path)
        except GMIError as e:
            return f"音乐生成失败: {e}"
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 音乐工具异常: {e}", exc_info=True)
            return f"音乐生成异常: {e}"
        return "音乐已生成并发送给用户。"

    @filter.llm_tool(name="gmi_text_to_speech")
    async def tool_text_to_speech(
        self, event: AstrMessageEvent, text: str, emotion: str = ""
    ):
        """用 MiniMax Speech 2.8 将文本合成为语音并直接发送给用户。当用户希望听到语音、朗读内容时调用。

        Args:
            text(string): 要合成的文本内容
            emotion(string): 情绪，可选值 auto/calm/happy/sad/angry/fearful/disgusted/surprised，留空为 auto
        """
        try:
            path = await self._generate_tts(text, emotion)
            await self._send_record(event, path)
        except GMIError as e:
            return f"语音合成失败: {e}"
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] TTS 工具异常: {e}", exc_info=True)
            return f"语音合成异常: {e}"
        return "语音已合成并发送给用户。"

    @filter.llm_tool(name="gmi_voice_clone_speech")
    async def tool_voice_clone(self, event: AstrMessageEvent, text: str):
        """用 MiniMax Voice Clone 以预设的克隆音色朗读文本并直接发送给用户。仅在用户明确要求使用克隆音色/特定人声说话时调用；需要管理员预先在插件配置中设置参考音频。

        Args:
            text(string): 要用克隆音色朗读的文本
        """
        try:
            path = await self._generate_voice_clone(text)
            await self._send_record(event, path)
        except GMIError as e:
            return f"音色克隆失败: {e}"
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 音色克隆工具异常: {e}", exc_info=True)
            return f"音色克隆异常: {e}"
        return "克隆语音已生成并发送给用户。"

    async def terminate(self):
        pass

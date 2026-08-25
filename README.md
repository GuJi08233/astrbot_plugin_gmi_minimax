# GMI MiniMax 音频插件

基于 [GMI Cloud](https://console.gmicloud.ai) 托管的 MiniMax 模型，为 AstrBot 提供音乐生成、语音合成和音色克隆能力。MiniMax Week 期间相关模型限时免费。

## 支持的模型

| 能力 | 模型 | 说明 |
|------|------|------|
| 音乐生成 | `minimax-music-3.0` | 歌词 + 风格描述生成完整歌曲，支持 `[Verse]`/`[Chorus]` 等结构标签 |
| 语音合成 | `minimax-tts-speech-2.8-turbo` / `-hd` | 30+ 音色、情绪/语速/音效控制 |
| 音色克隆 | `minimax-audio-voice-clone-speech-2.8-turbo` / `-hd` | 提供参考音频 URL 克隆任意音色说话 |

## 安装

插件界面右下角加号 → 从链接安装：

```
https://github.com/GuJi08233/astrbot_plugin_gmi_minimax
```

安装后在插件配置中填写 GMI Cloud API Key。

## 指令

```
/gmi music <风格描述>
<歌词，可用 [Verse]/[Chorus] 等标签>   # 多行：首行风格，余下歌词；单行时整体视为歌词

/gmi speech <文本>                     # 语音合成
/gmi clone <文本>                      # 用配置的参考音频克隆音色说话
/gmi clone <参考音频URL> <文本>         # 用指定参考音频克隆
/gmi tasks                             # 查看最近音乐任务的状态
/gmi models                            # 查询可用模型（验证 Key）
```

示例：

```
/gmi music 民谣 忧郁 木吉他 男声
[Verse]
路灯闪烁 晚风轻叹
影子拉长 独自向前
[Chorus]
推开木门 香气弥漫
熟悉角落 陌生视线
```

## LLM 工具

| 工具 | 功能 |
|------|------|
| `gmi_generate_music` | AI 按用户需求写歌词并生成歌曲发送 |
| `gmi_text_to_speech` | 将文本合成语音发送，支持情绪参数 |
| `gmi_voice_clone_speech` | 用预设克隆音色朗读文本发送 |
| `gmi_music_task_status` | 查询后台音乐任务进度（用户问"歌好了吗"时 AI 自动调用） |

对话示例：「帮我写一首关于秋天的民谣并唱出来」——AI 会自动写词并调用音乐工具。

## 配置说明

- **api_key**：GMI Cloud 的 API Key（必填）
- **music**：模型、格式（mp3/wav/pcm）、采样率、码率
- **tts**：模型（turbo/hd）、音色 ID、语速/音量/音调滑条、情绪、音效（回声/电话/机器人等）
- **voice_clone**：模型、默认参考音频 URL（公网可访问的 mp3/m4a/wav）、可选风格提示音频、降噪与音量归一化

生成的音频保存在 AstrBot 共享临时目录 `data/temp/` 下；文件以 base64 直传协议端发送，无需 AstrBot 与协议端（如 NapCat）共享文件系统。

## 说明

- 音乐生成在后台任务中执行（避免 AstrBot 工具 120 秒超时），触发后立即返回任务 ID，完成后自动发送并附任务 ID；瞬时服务端故障（Please try again）自动重试。语音 5-15 秒为同步执行。
- 音色克隆的参考音频必须是**公网 URL**（GMI 后端自动下载），本地文件暂不支持；实测参考音频时长需足够长（建议 10 秒以上），过短会报 `voice duration too short`。
- 文档标注 `voice_id` 可选，实测为必填——插件已自动生成唯一 voice_id，无需关心。

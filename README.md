# NewsDaily

## GitHub Actions 定时推送（科技 + 财经）

工作流：`.github/workflows/daily_tech_news.yml`  
脚本：`scripts/fetch_tech_news.py`

### Secrets（必填）

- **SERVERCHAN_SENDKEY**：Server酱 Turbo 的 SendKey/AppKey，用于推送到微信。
- **DEEPSEEK_API_KEY**：DeepSeek API Key，用于对过去24小时科技/财经资讯进行汇总（未配置则回退为原始列表推送）。

### Variables（可选）

- **DEEPSEEK_MODEL**：默认 `deepseek-chat`
- **DEEPSEEK_BASE_URL**：默认 `https://api.deepseek.com`
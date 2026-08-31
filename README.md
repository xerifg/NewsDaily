# NewsDaily

## GitHub Actions 定时推送（AI · 自动驾驶 · 机器人 技术日报）

每天定时（UTC 00:00 / 北京时间 08:00）抓取过去 24 小时 AI、自动驾驶、机器人/具身智能领域的资讯与论文，经 DeepSeek 汇总为五板块技术日报，通过 Server酱 推送到微信，并把全文归档到 `docs/` 目录。

工作流：`.github/workflows/daily_tech_news.yml`
脚本：`scripts/fetch_tech_news.py`
数据源与限额配置：`config/feeds.yaml`

### 内容板块

- **🤖 AI 大模型**：模型发布、训练/推理技术、开源动态（The Decoder、VentureBeat AI、Ars Technica AI、量子位、Hacker News、Reddit r/MachineLearning 等）
- **🚗 自动驾驶**：端到端/世界模型/VLA 技术路线、Robotaxi 落地、政策与供应链（Electrek、IEEE Spectrum、Google News、Reddit r/selfdrivingcars 等）
- **🦾 机器人与具身智能**：人形机器人、操作/导航、VLA 模型（IEEE Spectrum Robotics、Google News、Reddit r/robotics 等）
- **🔥 开源项目**：GitHub Trending 当日最火 Top 10 仓库（按今日新增 star 降序），不按领域过滤
- **📄 论文速递**：arXiv cs.RO / cs.CV 每日公告（按论文关键词过滤）+ HuggingFace Daily Papers 社区热榜

**板块内子组**：AI / 自动驾驶 / 机器人三个板块内部再按 **📰 新闻资讯** 与 **🧠 算法技术方案** 分组——
垂直源在配置里直接标注 `kind`（新闻媒体=新闻资讯，技术博客/官方技术博客=技术方案），综合源按 `kind_groups` 关键词自动归类（命中技术方案关键词→技术方案，否则默认新闻资讯）。

另外：监控关键开源仓库（openpilot、autoware、CARLA、Isaac Sim、lerobot）的新 Release；通过 Google News `site:` 查询覆盖新智元、机器之心、AIBase 等无官方 RSS 的中文 AI 媒体。

### 处理流程

```
垂直 RSS 源（The Decoder / VentureBeat / Ars Technica / MIT TR / Electrek /
IEEE Spectrum / Google News / Reddit 社区，配置标注 kind）─┐
综合源（量子位 / 雷锋网 / 新智元·机器之心·AIBase / Hacker News / The Verge 等，kind_groups 关键词归类）─┤→ 跨源标题相似度去重 → 按类别限量 → DeepSeek 分板块·分类型汇总
arXiv 分类 RSS + HuggingFace Daily Papers（论文关键词过滤）─┤        （未配置 LLM 时回退为原始列表）
GitHub Releases（关键仓库新 Release）─┘                        ↓
GitHub Trending（当日 Top 10 开源项目，独立板块）─────────────────────────────────→ 归档/推送
                                                                    ↓
                                        推送渠道（Server酱微信等，可扩展）
                                                                    ↓
                                        归档 docs/YYYY-MM-DD.md + 更新 docs/index.md
```

> 说明：arXiv 论文公告仅工作日更新，周末日报的论文板块由 HuggingFace 热榜补充；Reddit 对未登录请求限流较严，偶尔抓取失败会自动跳过，属正常现象。

### Secrets（必填）

- **SERVERCHAN_SENDKEY**：Server酱 Turbo 的 SendKey/AppKey，用于推送到微信。
- **DEEPSEEK_API_KEY**：DeepSeek API Key，用于对过去24小时资讯进行汇总（未配置则回退为原始列表推送）。

### Variables（可选）

- **DEEPSEEK_MODEL**：默认 `deepseek-chat`
- **DEEPSEEK_BASE_URL**：默认 `https://api.deepseek.com`
- **PUSH_CHANNELS**：推送渠道，逗号分隔，默认 `serverchan`
- **SITE_URL**：日报站点地址，默认 `https://xerifg.github.io/NewsDaily`
- **PUSH_MAX_BYTES**：推送正文字节上限，默认 `1000`（微信服务号模板消息约 1KB 会截断；走企业微信等长内容通道可调大）

### 推送策略：日报内容摘要 + 全文链接

为绕开微信推送的长度限制，LLM 分两次生成：先输出归档用全文，再单独生成微信推送用摘要版（脚本以 `<<<DIGEST>>>` 拼接）。两部分都以 **📋 日报内容摘要**（AI 概括的 3-4 条当日核心要点导读）开头：

- **归档**：全文写入 `docs/YYYY-MM-DD.md`，开头即日报内容摘要，随后是五个板块的完整内容；**每条要点标题为超链接**（`1. **[标题](链接)**`），链接均取自原始材料，AI 不编造
- **推送**：发摘要版（日报内容摘要 + AI/自动驾驶/机器人/论文每板块最多 4 条，标题即链接 `**[标题](链接)**` + 一句话概括，开源项目板块最多 10 条 `**[owner/repo](GitHub链接)**` + 简介）+ "查看完整日报"链接（指向 GitHub Pages 上的当日全文）
- **微信端长度保护**：微信服务号模板消息对正文有约 1KB 的截断限制，脚本会按字节数自动控制推送内容——摘要版（含链接）超出安全上限（默认 1000 字节）时降级为「日报内容摘要 + 链接」，保证微信端永远完整显示、链接不被截断。想推送完整板块要点，可配置 Server酱 企业微信等长内容通道，并调大环境变量 `PUSH_MAX_BYTES`
- 若 LLM 的摘要版漏掉了摘要块，脚本会自动从全文提取补到推送内容最前面

回退行为：LLM 未配置/失败时推送原始列表（超长截断并附全文链接）；LLM 输出中缺摘要部分时推送全文。

### 日报归档与 GitHub Pages

每次运行会把当天日报全文（不受推送长度截断影响）写入 `docs/YYYY-MM-DD.md`，并自动刷新 `docs/index.md` 索引（按日期倒序），随后由 workflow 提交回仓库。

要开启历史检索站点：仓库 **Settings → Pages → Source 选 "Deploy from a branch"、分支选 `main`、目录选 `/docs`**，之后 `https://<用户名>.github.io/<仓库名>/` 即为日报归档站（索引页为 `docs/index.md`）。

### 推送渠道扩展

推送渠道已抽象为接口：`PushChannel` 基类 + 渠道注册表。新增渠道只需两步：

1. 在 `scripts/fetch_tech_news.py` 中继承 `PushChannel`，实现 `push(title, desp)`（必要时覆写 `is_configured()`），并在 `CHANNELS` 注册表登记；
2. 把渠道名加进环境变量 `PUSH_CHANNELS`（如 `serverchan,telegram`）。

多个渠道并存时逐个推送，单个渠道失败不影响其他渠道，全部失败才报错。

### 本地调试

```bash
pip install -r requirements.txt
python scripts/fetch_tech_news.py --dry-run   # 只抓取并打印日报，不推送、不归档
python scripts/fetch_tech_news.py --config path/to/feeds.yaml  # 指定其他配置文件
```

### 自定义（改配置即可，无需动代码）

所有数据源与限额都在 `config/feeds.yaml`：

- 增删 RSS 源：`vertical_feeds`（垂直源，条目直接归类，可加 `kind: 新闻资讯/技术方案`）/ `generic_feeds`（综合源，关键词归类 + `kind_groups` 自动区分类型）
- 单个源的条数上限：给对应源加 `max_items` 字段；全局默认在 `limits.max_items_per_source`
- 调整关键词归类：`keyword_groups`；调整新闻/技术类型归类：`kind_groups`
- 调整论文过滤：`paper_keywords`；HuggingFace 热榜开关与条数：`huggingface_papers`
- 关注其他开源仓库：`github_repos`；GitHub Trending 开关/条数/是否关键词过滤：`github_trending`（默认 `filter_by_keywords: false`，即直接看当日最火 Top 10）
- 时间窗口与各类限额：`limits`

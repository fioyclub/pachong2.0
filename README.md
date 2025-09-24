# 足球赛事爬虫机器人

一个基于Python的异步足球赛事爬虫和Telegram机器人项目，能够自动获取足球比赛数据并通过Telegram机器人提供服务。

## 功能特性

- 🏈 **异步爬虫**: 高效获取足球赛事数据
- 🤖 **Telegram机器人**: 支持多种命令交互
- 💾 **智能缓存**: 内存缓存 + Redis缓存支持
- 🔄 **错误重试**: 完善的错误处理和重试机制
- 📊 **数据分析**: 比赛数据对比和投注建议
- 🚀 **云部署**: 支持Render平台部署

## 项目结构

```
爬虫2.0/
├── main.py                    # 主程序入口
├── config.py                  # 配置管理
├── scraper.py                 # 爬虫模块
├── advanced_scraper.py        # 高级爬虫功能
├── bot.py                     # Telegram机器人
├── cache_manager.py           # 缓存管理
├── error_handler.py           # 错误处理
├── models.py                  # 数据模型
├── requirements.txt           # 依赖包
├── render.yaml               # Render部署配置
├── .env.example              # 环境变量示例
└── test_scraper_standalone.py # 独立测试脚本
```

## 安装说明

### 1. 克隆项目

```bash
git clone <repository-url>
cd 爬虫2.0
```

### 2. 创建虚拟环境

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

## 配置说明

### 1. 环境变量配置

复制 `.env.example` 为 `.env` 并填写配置：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# Telegram机器人配置
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Redis配置（可选）
REDIS_URL=redis://localhost:6379
REDIS_PASSWORD=your_redis_password

# 爬虫配置
SCRAPER_TIMEOUT=30
SCRAPER_MAX_RETRIES=3
SCRAPER_DELAY=1

# 缓存配置
CACHE_EXPIRE_SECONDS=3600
CACHE_MAX_ENTRIES=1000
```

### 2. Telegram机器人设置

1. 在Telegram中找到 @BotFather
2. 发送 `/newbot` 创建新机器人
3. 按提示设置机器人名称和用户名
4. 获取机器人Token并填入 `.env` 文件

## 使用方法

### 1. 运行机器人

```bash
python main.py
```

### 2. 健康检查

```bash
python main.py --health-check
```

### 3. 测试爬虫

```bash
python main.py --test-scraper
```

### 4. 独立测试

```bash
python test_scraper_standalone.py
```

## Telegram机器人命令

- `/start` - 开始使用机器人
- `/check` - 查看今日足球赛事
- `/compare <team1> <team2>` - 比较两支球队
- `/bet <match_id>` - 获取投注建议
- `/help` - 查看帮助信息

## 部署到Render

### 1. 准备部署文件

项目已包含 `render.yaml` 配置文件，支持一键部署。

### 2. 在Render创建服务

1. 登录 [Render](https://render.com)
2. 连接GitHub仓库
3. 选择 "Web Service"
4. 配置环境变量
5. 部署服务

### 3. 环境变量配置

在Render控制台中设置以下环境变量：

- `TELEGRAM_BOT_TOKEN`
- `REDIS_URL`（如果使用Redis）
- 其他配置项

## 开发说明

### 项目架构

- **异步编程**: 使用 `asyncio` 和 `aiohttp` 实现高并发
- **模块化设计**: 各功能模块独立，便于维护
- **错误处理**: 完善的异常捕获和重试机制
- **缓存策略**: 多级缓存提升性能
- **配置管理**: 灵活的配置系统

### 扩展开发

1. **添加新的爬虫源**: 在 `scraper.py` 中添加新的爬取方法
2. **扩展机器人命令**: 在 `bot.py` 中添加新的命令处理器
3. **自定义缓存策略**: 修改 `cache_manager.py` 中的缓存逻辑
4. **增强错误处理**: 在 `error_handler.py` 中添加新的错误类型

## 故障排除

### 常见问题

1. **机器人无响应**
   - 检查 `TELEGRAM_BOT_TOKEN` 是否正确
   - 确认网络连接正常

2. **爬虫获取数据失败**
   - 检查目标网站是否可访问
   - 调整爬虫延迟和重试次数

3. **Redis连接失败**
   - 检查Redis服务是否运行
   - 确认 `REDIS_URL` 配置正确

### 日志查看

项目使用Python标准日志模块，日志级别可通过环境变量 `LOG_LEVEL` 设置。

## 贡献指南

1. Fork项目
2. 创建功能分支
3. 提交更改
4. 推送到分支
5. 创建Pull Request

## 许可证

本项目采用MIT许可证。详见 [LICENSE](LICENSE) 文件。

## 联系方式

如有问题或建议，请通过以下方式联系：

- 创建Issue
- 发送邮件
- Telegram群组

---

**注意**: 请遵守相关网站的robots.txt和使用条款，合理使用爬虫功能。
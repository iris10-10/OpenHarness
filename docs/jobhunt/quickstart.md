# 求职 Agent 快速开始

本指南用本地、离线数据走通 OpenHarness 的第一条求职工作流：配置画像、导入岗位、搜索岗位、匹配简历。

## 1. 安装

在项目根目录执行：

```bash
uv sync --extra dev
```

若需要启动 Web UI，再安装 Web 依赖：

```bash
uv sync --extra web
```

## 2. 首次配置

```bash
oh job-hunt setup
```

无交互环境可以使用默认值：

```bash
oh job-hunt setup --yes --embedding-provider hash \
  --cities 杭州 --positions "Python 后端工程师" \
  --skills "Python,FastAPI,MySQL,Redis"
```

`hash` embedding 是无网络的确定性降级实现，适合本地试用和测试。生产环境可以改用 `openai` 或已安装的本地 embedding provider。

## 3. 导入示例岗位

```bash
oh job-hunt import \
  --dir ./examples/sample_data/jobs \
  --collection jobs \
  --category 后端开发
```

## 4. 搜索与匹配

```bash
oh job-hunt search --query "Python 后端" --top 5
oh job-hunt match \
  --resume ./examples/sample_data/resume.md \
  --jd ./examples/sample_data/jobs/python-backend.md
```

## 5. 运行演示

```bash
python examples/demo_scripts/run_demo.py
```

演示只读取仓库内的示例文本，并输出确定性的匹配摘要，不会调用模型或外部网络。

## 常见下一步

- 查看画像：`oh job-hunt profile show`
- 更新画像：`oh job-hunt profile update --skills "Python,FastAPI"`
- 进入交互式求职对话：`oh job-hunt chat`
- 启动 Web UI：`oh job-hunt web --port 8000`
- 查看投递看板：`oh job-hunt applications list`

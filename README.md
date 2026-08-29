# DP_CFG

DP_CFG 是一个面向 Java 项目的自动化测试生成与评估工具。

## 环境要求

- Python 3.10 或 3.11
- JDK 和 Maven
- Defects4J（仅在处理 Defects4J 项目时需要）

## 安装

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 配置

根据本地运行环境完成项目配置。API 密钥等敏感信息应通过环境变量提供，不要写入仓库。

## 运行

```powershell
python main.py
```

## 测试

```powershell
python -m pytest -q
```

本地数据、运行结果、环境文件和内部文档均由 `.gitignore` 排除。

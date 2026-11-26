# Dorset Care · 养老院治疗预约系统（Python FastAPI MVP）

面向养老院院方员工的治疗预约管理最小可操作版本（MVP），全部使用虚构测试数据，支持预约清单筛选、时间冲突拦截、改期事务回滚保护、软删除取消及多进程并发抢订控制。

> 需求分析（待确认问题 / 工程假设 / 第一版边界）与完整验证及 AI 使用记录详见交付邮件说明。

---

## 技术栈与目录结构

- **后端**：Python 3.11+ · FastAPI + Uvicorn + 内置 `sqlite3`（WAL 模式）
- **前端**：零构建原生 HTML / CSS / JavaScript（由 FastAPI 直接托管）
- **测试**：Python 内置 `unittest` + `multiprocessing` 多进程并发压测

```text
dorset-care-booking/
├── app.py                     # 一键启动入口 (http://127.0.0.1:8000)
├── requirements.txt           # Python 依赖
├── backend/
│   ├── db.py                  # SQLite WAL / busy_timeout / BEGIN IMMEDIATE 事务封装与示例数据
│   ├── appointments.py        # 核心业务规则（时长校验、半开区间冲突判定、改期回滚、软删除、乐观锁）
│   └── main.py                # FastAPI 接口路由与统一错误处理
├── static/
│   ├── index.html             # 员工端页面（含内置并发抢订演示卡片）
│   ├── styles.css             # 页面样式
│   └── app.js                 # 前端交互逻辑
└── tests/
    ├── test_appointments.py   # 8 项领域与 API 契约单元测试
    └── test_concurrency.py    # 6 个独立 OS 进程并发抢订压测
```

---

## 快速启动

```bash
pip install -r requirements.txt
python app.py
```

启动后访问：
- 应用界面：`http://127.0.0.1:8000`
- Swagger API 文档：`http://127.0.0.1:8000/docs`

首次启动会自动在 `data/appointments.db` 初始化 SQLite 数据库并写入虚构的治疗师、住民及明日示例预约；关闭重启后所有变更自动持久化保留。

---

## 运行自动化测试

```bash
# 1. 领域逻辑与 HTTP 409 报错契约测试（8 项，含失败与回滚场景）
python -m unittest discover -s tests -p "test_appointments.py" -v

# 2. 多进程并发抢订压测（6 个独立 OS 进程同毫秒抢订同一治疗师同一时段）
python tests/test_concurrency.py
```

---

## 核心 API 一览

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `GET` | `/api/meta` | 获取虚构治疗师、住民清单及支持时长（30/45/60 分钟） |
| `GET` | `/api/appointments` | 按 `status` / `therapistId` / `residentId` 筛选预约清单 |
| `POST` | `/api/appointments` | 新增预约（冲突返回 `409` 及具体冲突码） |
| `PATCH` | `/api/appointments/{id}/reschedule` | 改期预约（携带 `expectedVersion`，冲突或版本过期时事务回滚保留原预约） |
| `POST` | `/api/appointments/{id}/cancel` | 取消预约（软删除为 `CANCELLED` 并释放时段） |

---

## 并发抢订控制说明（深入项 ③）

1. **数据库级写锁串行化**：`backend/db.py` 开启 `PRAGMA journal_mode = WAL` 与 `PRAGMA busy_timeout = 5000`，所有写操作显式使用 `BEGIN IMMEDIATE` 事务，进事务即获取 `RESERVED` 写锁，避免并发下的“先查后写”重复预约与锁升级死锁。
2. **改期原子回滚**：改期在同一 `BEGIN IMMEDIATE` 事务内完成版本校验、排除自身后的区间重叠检测与更新，失败即 `ROLLBACK`，原预约完整保留。
3. **乐观锁防覆盖**：每条预约维护递增 `version`，前端改期/取消携带 `expectedVersion`，过期版本直接返回 `409 VERSION_CONFLICT`。
4. **多实例演进方向**：生产环境多节点部署可迁移至 PostgreSQL，使用 `btree_gist` 排他约束（`EXCLUDE USING gist`）在数据库引擎层保证跨实例强一致。

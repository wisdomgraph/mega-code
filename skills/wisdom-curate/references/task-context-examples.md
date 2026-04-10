# TASK_CONTEXT examples

Reference examples for composing the `TASK_CONTEXT` string in Step 2b.
Each example is a single descriptive sentence covering language, version
(when visible in the manifest), and the key frameworks/libraries.

- `Python 3.12 project using FastAPI, SQLAlchemy, Celery`
- `TypeScript project using Next.js 14, Prisma, TailwindCSS`
- `Go 1.22 project using Gin, GORM`
- `Java 21 Maven project using Spring Boot 3, JPA`
- `Rust project using Axum, Tokio, SQLx`
- `Ruby project using Rails 7, Sidekiq`

Rules:

- Include version numbers only when they are visible in the manifest.
- If only the language is clear: `Python project`.
- If the project type is unrecognizable, leave `TASK_CONTEXT` empty.

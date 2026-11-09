# Git-структура Moon Light

- `main` — исходная стабильная версия проекта, импортированная для этого учебного репозитория.
- `web/flask` — ветка Flask-веб-версии.
- `feature/booking-management` — отдельная ветка сценариев бронирования.
- `feature/admin-reports` — отдельная ветка отчётности администратора.
- `feature/profile-security` — отдельная ветка профиля и безопасности.

Все feature-ветки создаются от `web/flask`, имеют собственный коммит и затем сливаются обратно через `--no-ff`.

Полезные команды:
```bash
git branch
git log --oneline --graph --decorate --all
git switch web/flask
git switch -c feature/new-task
git add .
git commit -m "feat: describe change"
git switch web/flask
git merge --no-ff feature/new-task
```

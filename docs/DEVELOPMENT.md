# Разработка

React / TypeScript / Vite / Radix UI; FastAPI, Decimal, SQLite. Windows x64. Сначала запустите START.bat: он загрузит pinned runtime и зависимости в папку проекта.

Из PowerShell в корне распакованного проекта:

```powershell
.\.cache\runtimes\python\python.exe -B scripts/project.py test
.\.cache\runtimes\python\python.exe -B scripts/project.py build
.\.cache\runtimes\python\python.exe -B scripts/test_project_boundary.py
```

`scripts/project.py start` запускает отдельный MOCK без сети Ozon; предварительно остановите основной сервер. `start-read` запускает рабочий режим. `dev` открывает frontend на порту 5173. Не передавайте ключи через frontend или VITE-переменные.

Проверка npm advisories: `python -B scripts/audit_dependencies.py`. Упаковка: `python -B scripts/package_release.py`. Используйте проектный Python вместо `python`, если он отсутствует в PATH. Сборщик использует закрытый список файлов, проверяет ссылки/junction, исключает настройки и данные, сканирует токены и проверяет ZIP после записи. Это дополнительная проверка, а не гарантия обнаружения всех возможных секретов.

Цены считает сервер через Decimal; SKU, product_id и offer_id различаются. Запись выполняется по сохранённому неизменяемому плану после Fresh Check и явного подтверждения. UNKNOWN не повторяется автоматически. Откат — отдельный план. Подробные рабочие данные сохраняются только зашифрованными; в открытых логах и тестах допустимы лишь синтетические данные.

В исходный архив входят тесты. Исторические внутренние отчёты, снимки реальных чтений, локальные базы, кеши, виртуальные окружения и одноразовая диагностика исключены. Шрифт Golos Text распространяется по включённой `frontend/public/fonts/OFL.txt`. Лицензия на исходный код автором пока не выбрана.

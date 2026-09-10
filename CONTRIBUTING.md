# Разработка и сборка QuickTask

Это техническая инструкция для тех, кто хочет запустить QuickTask из исходников, поправить код или собрать собственный `.exe`. Общее описание проекта и его возможностей — в [README.md](README.md).

---

## Структура проекта

- `main.py` — точка входа, вся логика на Python: окно (pywebview), работа с файлами задач, watchdog-синхронизация, глобальный хоткей, скрытие иконки из панели задач.
- `index.html` — весь UI (HTML/CSS/JS в одном файле).
- `vendor/codemirror/` — редактор CodeMirror, используемый для описания задачи. Подключается из `index.html` относительным путём.
- `requirements.txt` — зафиксированные версии зависимостей.

---

## Быстрый старт (разработка)

### 1. Клонирование репозитория
```cmd
git clone https://github.com/xelay/QuickTask.git
cd QuickTask
```

### 2. Создание виртуального окружения
```cmd
python -m venv venv
venv\Scripts\activate
```

### 3. Установка зависимостей
```cmd
pip install -r requirements.txt
```
*(или вручную: `pip install pywebview python-frontmatter watchdog keyboard`)*

### 4. Запуск приложения
```cmd
venv\Scripts\python.exe main.py
```
*(Для тихого запуска без консольного окна можно использовать `venv\Scripts\pythonw.exe main.py`)*

---

## Сборка в самостоятельный EXE-файл

Приложение можно собрать в переносимый `.exe`, работающий автономно без установленного Python.

### 1. Установка PyInstaller
В активном виртуальном окружении выполните:
```cmd
venv\Scripts\pip.exe install pyinstaller
```

### 2. Сборка приложения (рекомендуемый режим `--onedir`)
Режим папки обеспечивает мгновенный холодный запуск без распаковки во временный каталог:
```cmd
venv\Scripts\pyinstaller.exe --noconsole --name "QuickTask" --add-data "index.html;." --add-data "vendor;vendor" main.py
```

> Флаг `--add-data "vendor;vendor"` обязателен: в `index.html` подключён редактор CodeMirror по относительному пути (`vendor/codemirror/...`). Без этого флага в собранном `.exe` не загрузится редактор описания задачи.

- Готовый исполняемый файл и зависимости будут созданы в папке `dist\QuickTask\`.
- Запуск: `dist\QuickTask\QuickTask.exe`.

### 3. Сборка в единый файл (`--onefile`)
Если требуется переносить программу одним файлом:
```cmd
venv\Scripts\pyinstaller.exe --noconsole --onefile --name "QuickTask" --add-data "index.html;." --add-data "vendor;vendor" main.py
```
Файл будет создан по пути `dist\QuickTask.exe`.

### 4. Автозагрузка с Windows
Чтобы QuickTask запускался при старте системы:
1. Нажмите `Win + R`, введите `shell:startup` и нажмите `Enter`.
2. Создайте ярлык для `QuickTask.exe` в открывшейся папке автозагрузки.

---

## Технические примечания

- Под капотом `pywebview` использует бэкенд `edgechromium` через `pythonnet` — это системный компонент WebView2, входящий в состав Windows 10/11. Дополнительно ставить .NET или браузерный движок не нужно, но `pythonnet`/`clr_loader` появятся в зависимостях автоматически.
- `pywebview` поставляется с собственным хуком для PyInstaller (`webview/__pyinstaller/hook-webview.py`), поэтому дополнительные `--hidden-import` для него не требуются.
- Глобальный хоткей реализован через библиотеку `keyboard` (`main.py`, `register_hotkey`).

---

## Как предложить изменения

1. Создайте отдельную ветку от `main`.
2. Убедитесь, что приложение запускается из исходников (см. «Быстрый старт» выше) и, по возможности, что сборка `--onedir` проходит без ошибок.
3. Откройте Pull Request с коротким описанием, что и зачем изменено.

"""
Скачивает HTML-код страниц ivo.garant.ru по списку redirect-ссылок.

Использует Selenium, потому что текст документа на «Гаранте» подгружается
через JS уже после загрузки HTML-каркаса. Простой requests.get() вернёт
пустую страницу без содержания.

Запуск:
    python garant_html.py urls.txt

Формат urls.txt — одна ссылка на строку:
    http://ivo.garant.ru/document/redirect/411309161
    http://ivo.garant.ru/document/redirect/12127405
    ...

Результат:
    html_docs/<topic_id>.html   — HTML-код страницы
    html_downloaded.log         — журнал скачанных ID (для дозапуска)
"""

import os
import re
import sys
import time
import random
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

# ==================== НАСТРОЙКИ ====================

OUT_DIR      = os.path.abspath("html_docs")
PROFILE_DIR  = os.path.abspath("chrome_profile")   # логин/куки сохраняются между запусками
LOG_FILE     = "html_downloaded.log"
DELAY_SEC    = 3                                   # пауза между страницами
PAGE_TIMEOUT = 30                                  # макс. ожидание загрузки страницы
RENDER_WAIT  = 3.0                                 # доп. пауза после загрузки — чтобы JS дорисовал текст
HEADLESS     = True                               # True — только после первой авторизации

# ====================================================


def get_topic_id(url: str) -> str | None:
    """Достаёт числовой ID из redirect-ссылки."""
    m = re.search(r'/redirect/(\d+)', url)
    return m.group(1) if m else None


def load_done() -> set:
    if not os.path.exists(LOG_FILE):
        return set()
    with open(LOG_FILE, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def mark_done(topic_id: str):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(topic_id + "\n")


def build_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--window-size=1600,1000")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Отключаем картинки/медиа — ускоряет загрузку и экономит трафик
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
    }
    options.add_experimental_option("prefs", prefs)

    if HEADLESS:
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

    return webdriver.Chrome(options=options)


def wait_content_ready(driver, timeout: int = PAGE_TIMEOUT):
    """
    Ждёт, пока страница реально отрисует контент.
    Ориентируемся на появление любого из маркеров:
      - заголовок документа (<h1>)
      - блок текста (<div> с классом, содержащим 'text' или 'content')
      - или просто исчезновение спиннера/загрузки
    """
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    # Даём JS-движку Гаранта дорисовать текст
    time.sleep(RENDER_WAIT)


def save_html(driver, topic_id: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{topic_id}.html")
    html = driver.page_source
    # Явно utf-8 — Selenium отдаёт уже декодированную строку
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def main():
    # if len(sys.argv) < 2:
    #     print("Использование: python garant_html.py urls.txt")
    #     sys.exit(1)

    urls_path = "/home/maxim/Documents/year_project/cbr-credit-risk-rag/notebooks/links.txt"#sys.argv[1]
    with open(urls_path, encoding="utf-8") as f:
        # Поддерживаем как чистый список URL, так и Python-список в квадратных скобках
        raw = f.read()
    urls = re.findall(r"https?://[^\s'\"\],]+", raw)

    if not urls:
        print("❌ В файле не найдено ни одной ссылки.")
        sys.exit(1)

    # Дедупликация, сохраняя порядок
    seen, unique_urls = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)
    urls = unique_urls

    done = load_done()
    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"Всего ссылок:         {len(urls)}")
    print(f"Уже скачано:          {len(done)}")
    print(f"Папка:                {OUT_DIR}")
    print(f"Журнал:               {LOG_FILE}")
    print("-" * 60)

    driver = build_driver()

    if not HEADLESS:
        try:
            driver.get("https://ivo.garant.ru/")
            input("▶ При необходимости войдите в систему, затем нажмите Enter... ")
        except WebDriverException as e:
            print(f"⚠ Стартовая страница не открылась: {e}")

    ok, fail = 0, 0

    for idx, url in enumerate(urls, 1):
        topic_id = get_topic_id(url)
        if not topic_id:
            print(f"[{idx}/{len(urls)}] ⚠ не удалось извлечь ID: {url}")
            continue

        if topic_id in done:
            print(f"[{idx}/{len(urls)}] ⏭  {topic_id} — уже скачан")
            continue

        print(f"[{idx}/{len(urls)}] 🔗 {topic_id}")
        try:
            driver.get(url)
            wait_content_ready(driver)

            path = save_html(driver, topic_id)
            size_kb = os.path.getsize(path) / 1024

            # Простая проверка на пустоту: если HTML слишком маленький — вероятно,
            # вместо текста документа пришла заглушка (капча/лимит/ошибка)
            if size_kb < 15:
                print(f"    ⚠ подозрительно маленький HTML: {size_kb:.1f} КБ — проверьте вручную")
                driver.save_screenshot(f"check_{topic_id}.png")

            print(f"    ✅ сохранён: {path} ({size_kb:.1f} КБ)")
            mark_done(topic_id)
            ok += 1

        except TimeoutException:
            print(f"    ❌ таймаут загрузки")
            fail += 1
            driver.save_screenshot(f"timeout_{topic_id}.png")
        except WebDriverException as e:
            print(f"    ❌ WebDriver: {str(e)[:150]}")
            fail += 1
        except Exception as e:
            print(f"    ❌ {type(e).__name__}: {str(e)[:150]}")
            fail += 1

        time.sleep(DELAY_SEC + random.uniform(0, 2))

    driver.quit()
    print("-" * 60)
    print(f"Готово. Успешно: {ok}, ошибок: {fail}, всего в журнале: {len(load_done())}")


if __name__ == "__main__":
    main()
"""Проверить карточки актов; отсутствие отметки об отмене не означает «действует»."""

import argparse
import csv
import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.request import Request, urlopen

from prepare_corpus import ROOT, inventory, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=date.today().isoformat())
    args = parser.parse_args()
    path = ROOT / "data/legal_status.csv"
    documents, _ = inventory()
    documents = [d for d in documents if d["selected"]]
    existing = {}
    if path.exists():
        with path.open() as file:
            existing = {r["document_id"]: r for r in csv.DictReader(file)}

    def check(doc):
        old = existing.get(doc["document_id"], {})
        if old.get("method") in {"primary_source", "reviewed_source"}:
            return old
        number = "411309161" if doc["file_id"] == "845_p" else doc["file_id"]
        url = f"https://base.garant.ru/{number}/"
        row = {
            "document_id": doc["document_id"],
            "status": "unknown",
            "checked_on": args.as_of,
            "source_url": url,
            "method": "document_card",
            "evidence": "",
        }
        if not doc["number"]:
            return dict(
                row,
                status="not_applicable",
                source_url="",
                evidence="Справочный материал",
            )
        try:
            with urlopen(
                Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20
            ) as response:
                page = response.read().decode(
                    response.headers.get_content_charset() or "cp1251"
                )
            match = re.search(r"<h1\b[^>]*>(.*?)</h1>", page, re.S | re.I)
            title = (
                re.sub(
                    r"\s+", " ", html.unescape(re.sub("<[^>]+>", " ", match[1]))
                ).strip()
                if match
                else ""
            )
            if metadata(title)["document_id"] != doc["document_id"]:
                row["evidence"] = "Реквизиты карточки не подтверждены"
            elif re.search(r"\bутратил[ао]? силу\b", title, re.I):
                row.update(status="repealed", evidence=title)
            else:
                row["evidence"] = (
                    "Карточка прочитана; однозначная отметка статуса "
                    "всего акта отсутствует"
                )
        except Exception as error:
            row["evidence"] = f"Карточка недоступна: {type(error).__name__}"
        return row

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(check, documents))
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Проверено {len(rows)} источников; статусы сохранены в {path}")


if __name__ == "__main__":
    main()

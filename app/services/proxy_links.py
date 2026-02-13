from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode


@dataclass(slots=True)
class ProxyItem:
    name: str
    server: str
    port: int
    secret: str
    enabled: bool = True

    @property
    def tme_link(self) -> str:
        query = urlencode(
            {
                "server": self.server,
                "port": self.port,
                "secret": self.secret,
            }
        )
        return f"https://t.me/proxy?{query}"

    @property
    def tg_link(self) -> str:
        query = urlencode(
            {
                "server": self.server,
                "port": self.port,
                "secret": self.secret,
            }
        )
        return f"tg://proxy?{query}"


class ProxyStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load_all(self) -> list[ProxyItem]:
        if not self.path.exists():
            return []

        # Accept UTF-8 with or without BOM to avoid Windows editor issues.
        raw_data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        proxies: list[ProxyItem] = []
        for item in raw_data:
            proxies.append(
                ProxyItem(
                    name=item["name"],
                    server=item["server"],
                    port=int(item["port"]),
                    secret=item["secret"],
                    enabled=bool(item.get("enabled", True)),
                )
            )
        return proxies

    def load_enabled(self) -> list[ProxyItem]:
        return [proxy for proxy in self.load_all() if proxy.enabled]

    def save_all(self, proxies: Iterable[ProxyItem]) -> None:
        payload = [asdict(proxy) for proxy in proxies]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def toggle_enabled(self, name: str, value: bool) -> bool:
        proxies = self.load_all()
        changed = False
        for proxy in proxies:
            if proxy.name == name:
                proxy.enabled = value
                changed = True
                break

        if changed:
            self.save_all(proxies)
        return changed


def build_share_text(proxy: ProxyItem) -> str:
    return (
        f"MTProto proxy для Telegram: {proxy.name}\n"
        f"Подключить: {proxy.tme_link}\n"
        f"tg:// ссылка: {proxy.tg_link}\n\n"
        "Это прокси только для Telegram (не VPN)."
    )

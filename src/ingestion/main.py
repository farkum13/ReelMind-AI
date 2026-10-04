from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from unicodedata import name as unicode_name
from xml.etree import ElementTree

from langdetect import DetectorFactory, LangDetectException, detect_langs

DetectorFactory.seed = 0

GOOGLE_TRENDS_RSS = "https://trends.google.com/trending/rss"
GOOGLE_TRENDS_NAMESPACE = "https://trends.google.com/trending/rss"


def _matches_language(text: str, language: str) -> bool:
    try:
        return any(
            result.lang == language and result.prob >= 0.70
            for result in detect_langs(text)
        )
    except LangDetectException:
        return False


def _uses_latin_script(text: str) -> bool:
    letters = [character for character in text if character.isalpha()]
    return bool(letters) and all(
        "LATIN" in unicode_name(character, "") for character in letters
    )


def get_trends(geo: str = "IN", language: str = "en", hours: int = 24) -> list[dict]:
    if hours <= 0:
        raise ValueError("hours must be greater than 0")

    url = f"{GOOGLE_TRENDS_RSS}?{urlencode({'geo': geo})}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=15) as response:
        root = ElementTree.fromstring(response.read())

    namespace = f"{{{GOOGLE_TRENDS_NAMESPACE}}}"
    trends = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    for item in root.findall("./channel/item"):
        title = item.findtext("title")
        published_at = item.findtext("pubDate")
        if not title or not published_at:
            continue

        published = parsedate_to_datetime(published_at)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if not cutoff <= published.astimezone(timezone.utc) <= now:
            continue

        articles = item.findall(f"{namespace}news_item")
        article_titles = [
            article.findtext(f"{namespace}news_item_title") for article in articles
        ]
        if language == "en" and not _uses_latin_script(title):
            continue
        if not (
            _matches_language(title, language)
            or any(
                article_title and _matches_language(article_title, language)
                for article_title in article_titles
            )
        ):
            continue

        news = []
        for article, article_title in zip(articles, article_titles):
            if not article_title or not _matches_language(article_title, language):
                continue
            news.append({
                "title": article_title,
                "source": article.findtext(f"{namespace}news_item_source"),
                "url": article.findtext(f"{namespace}news_item_url"),
            })

        trends.append({
            "title": title,
            "approx_traffic": item.findtext(f"{namespace}approx_traffic"),
            "published_at": published_at,
            "news": news,
        })

    return trends
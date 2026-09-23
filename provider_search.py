"""Provider API adapters based on example/naverkakaoridi.py.

Only search/metadata parsing is reused; persistence belongs to Rabbit Plugins.
"""
import html
import re
import json
import hashlib
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DEFAULT_USER_AGENT = "Mozilla/5.0 Chrome/126 Safari/537.36"
NOVELPIA_FAILURE_COOLDOWN = 300
SOURCE_KINDS = {"naver_webtoon": "manhwa", "kakaopage": ("novel", "manhwa"), "kakao_webtoon": "manhwa",
                "munpia": "novel", "novelpia": "novel"}
NOVEL_GENRES = {"웹소설", "판타지", "무협", "현대", "로맨스", "현대판타지", "라이트노벨", "공포", "SF", "스포츠", "대체역사", "기타"}
SOURCE_LABELS = {"naver_webtoon": "네이버웹툰", "kakao_webtoon": "카카오웹툰", "kakaopage": "카카오페이지",
                 "munpia": "문피아", "novelpia": "노벨피아"}


class SearchAdapter:
    _cache_lock = threading.Lock()
    _source_cooldowns = {}

    def __init__(self, matches):
        self.matches = matches

    def _normalize(self, value):
        return value

    def _matches_candidate_title(self, query, title, cfg):
        return self.matches(query, title)

    def _parallel_map(self, func, values):
        if not values:
            return []
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(func, values))

    def _proxy_url(self, cfg):
        return ""

    def _request(self, url, cfg, headers=None, method=None, data=None, timeout=None):
        request = urllib.request.Request(url, data=data, method=method,
            headers={"User-Agent": DEFAULT_USER_AGENT, **(headers or {})})
        with urllib.request.urlopen(request, timeout=timeout or 8) as response:
            return response.read(4 * 1024 * 1024), response

    def _get_json(self, url, cfg, headers=None):
        body, response = self._request(url, cfg, headers)
        return json.loads(body.decode(response.headers.get_content_charset() or "utf-8"))

    def _clean_text(self, value):
        if isinstance(value, dict):
            value = value.get("name") or value.get("title") or ""
        if isinstance(value, (list, tuple)):
            return ", ".join(filter(None, (self._clean_text(v) for v in value)))
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))).strip()

    def _kakao_author_text(self, authors):
        if isinstance(authors, list):
            return self._join_names([a for a in authors
                if not isinstance(a, dict) or a.get("type") != "PUBLISHER"])
        return self._clean_text(authors)

    def _item(self, source, title, author, publisher, cover, description, link,
              genre, tags, pub_date, score, isbn="", **extra):
        # Update timestamps and platform ratings are not publication dates or prose.
        return dict(title=self._clean_text(title), author=self._clean_text(author),
                    publisher=self._clean_text(publisher), cover=cover or "",
                    summary=self._clean_text(description), url=link, link=link,
                    genre=self._clean_text(genre), tags=self._clean_text(tags), isbn=isbn, **extra)

    def _search_naver_webtoon(self, query, cfg):
        url = "https://comic.naver.com/api/search/all?" + urllib.parse.urlencode({"keyword": query})
        data = self._get_json(url, cfg, headers=self._naver_webtoon_headers("https://comic.naver.com/"))
        groups = ("searchWebtoonResult", "searchBestChallengeResult", "searchChallengeResult")
        candidates = []
        seen_ids = set()
        max_results = self._int(cfg.get("MAX_RESULTS"), 20, 1, 100)
        for group in groups:
            value = data.get(group) or {}
            rows = value.get("searchViewList") or [] if isinstance(value, dict) else value
            for item in rows if isinstance(rows, list) else []:
                title_id = item.get("titleId")
                title = self._clean_text(item.get("titleName") or item.get("title"))
                if not title_id or not title or not self._matches_candidate_title(query, title, cfg):
                    continue
                if (item.get("adult") or item.get("nineteen")) and not self._truthy(cfg.get("INCLUDE_ADULT")):
                    continue
                if str(title_id) in seen_ids:
                    continue
                seen_ids.add(str(title_id))
                candidates.append((item, title_id))
                if len(candidates) >= max_results:
                    break
            if len(candidates) >= max_results:
                break

        details = self._parallel_map(
            lambda candidate: self._naver_webtoon_detail(candidate[1], cfg),
            candidates,
        )
        results = []
        for (item, title_id), detail in zip(candidates, details):
            merged = dict(item)
            merged.update(detail)
            rest = bool(merged.get("rest"))
            finished = bool(merged.get("finished"))
            status = "1" if rest else "2" if finished else "0"
            curation = merged.get("curationTagList") or merged.get("tagList") or []
            genre_values = [
                tag for tag in curation
                if isinstance(tag, dict) and str(tag.get("curationType") or "").startswith("GENRE_")
            ]
            tag_values = [
                tag for tag in curation
                if not isinstance(tag, dict) or not str(tag.get("curationType") or "").startswith("GENRE_")
            ]
            if not genre_values:
                genre_values = merged.get("genreList") or []
            age = merged.get("age") if isinstance(merged.get("age"), dict) else {}
            age_type = str(age.get("type") or "").upper()
            books_lv = {
                "RATE_ALL": "everyone", "RATE_12": "ma15+", "RATE_15": "ma15+",
                "RATE_18": "adult only", "RATE_19": "adult only",
            }.get(age_type, "")
            first_date = self._naver_webtoon_date(merged.get("_first_service_date"))
            last_date = self._naver_webtoon_date(merged.get("lastArticleServiceDate"))
            total = self._int(
                merged.get("_article_total") or merged.get("articleTotalCount"), 0, 0, 1000000)
            results.append(self._item(
                source="네이버웹툰",
                title=merged.get("titleName") or "",
                author=self._join_names(merged.get("communityArtists")) or merged.get("displayAuthor") or "",
                publisher="네이버웹툰",
                cover=merged.get("thumbnailUrl") or merged.get("posterThumbnailUrl") or "",
                description=merged.get("synopsis") or "",
                link=f"https://comic.naver.com/webtoon/list?titleId={title_id}",
                genre=self._join_names(genre_values),
                tags=self._join_names(tag_values),
                pub_date=first_date,
                score="",
                release_date=first_date,
                publication_start_date=first_date,
                publication_end_date=last_date if finished else "",
                publication_status=status,
                total_chapters=total if finished else 0,
                books_lv=books_lv,
            ))
        return results

    def _naver_webtoon_detail(self, title_id, cfg):
        headers = self._naver_webtoon_headers(
            f"https://comic.naver.com/webtoon/list?titleId={title_id}")
        detail = {}
        try:
            detail = self._get_json(
                "https://comic.naver.com/api/article/list/info?" +
                urllib.parse.urlencode({"titleId": title_id}), cfg, headers=headers)
        except Exception:
            pass
        try:
            chronology = self._get_json(
                "https://comic.naver.com/api/article/list?" +
                urllib.parse.urlencode({"titleId": title_id, "page": 1, "sort": "ASC"}),
                cfg, headers=headers)
            articles = chronology.get("articleList") or []
            if articles:
                detail["_first_service_date"] = articles[0].get("serviceDateDescription") or ""
            detail["_article_total"] = chronology.get("totalCount") or 0
        except Exception:
            pass
        return detail

    @staticmethod
    def _naver_webtoon_headers(referer):
        return {
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
        }

    @staticmethod
    def _naver_webtoon_date(value):
        text = str(value or "").strip()
        match = re.fullmatch(r"(\d{2}|\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", text)
        if not match:
            return ""
        year = int(match.group(1))
        if year < 100:
            current = time.localtime().tm_year % 100
            year += 2000 if year <= current else 1900
        return f"{year:04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

    def _search_kakao_webtoon(self, query, cfg):
        params = urllib.parse.urlencode({"word": query, "offset": 0, "limit": self._int(cfg.get("MAX_RESULTS"), 20, 1, 100)})
        url = f"https://gateway-kw.kakao.com/search/v2/content?{params}"
        data = self._get_json(url, cfg, headers=self._kakao_webtoon_headers(cfg, query))
        contents = ((data.get("data") or {}).get("content")) or []

        normalized_query = self._normalize(query)
        candidates = []
        max_results = self._int(cfg.get("MAX_RESULTS"), 20, 1, 100)
        seen_ids = set()
        for item in contents:
            if item.get("adult") and not self._truthy(cfg.get("INCLUDE_ADULT")):
                continue
            content_id = item.get("id") or item.get("contentId")
            title = self._clean_text(item.get("title"))
            if not content_id or not self._matches_candidate_title(normalized_query, title, cfg):
                continue
            content_key = str(content_id)
            if content_key in seen_ids:
                continue
            seen_ids.add(content_key)
            candidates.append((item, content_id))
            if len(candidates) >= max_results:
                break

        details = self._parallel_map(
            lambda candidate: self._kakao_webtoon_detail(candidate[1], cfg),
            candidates,
        )
        results = []
        for (item, content_id), detail in zip(candidates, details):
            merged = dict(item)
            merged.update(detail)
            title = self._clean_text(merged.get("title"))
            if not title or not content_id:
                continue
            seo_id = urllib.parse.quote(str(merged.get("seoId") or title.replace(" ", "-")), safe="")
            results.append(
                self._item(
                    source="카카오웹툰",
                    title=title,
                    author=self._kakao_author_text(merged.get("authors")),
                    publisher=self._kakao_publisher(merged.get("authors")) or "카카오웹툰",
                    cover=merged.get("thumbnailImage") or merged.get("sharingThumbnailImage") or merged.get("titleImageA") or merged.get("backgroundImage"),
                    description=merged.get("synopsis") or merged.get("catchphraseThreeLines") or merged.get("catchphraseTwoLines") or "",
                    link=f"https://webtoon.kakao.com/content/{seo_id}/{content_id}",
                    genre=merged.get("genre") or "",
                    tags="",
                    pub_date="",
                    score=str(merged.get("rating") or ""),
                )
            )
        return results

    def _kakao_webtoon_detail(self, content_id, cfg):
        url = f"https://gateway-kw.kakao.com/decorator/v2/decorator/contents/{content_id}"
        try:
            data = self._get_json(url, cfg, headers=self._kakao_webtoon_headers(cfg, ""))
            return data.get("data") or {}
        except Exception:
            return {}

    def _search_kakaopage(self, query, cfg):
        category = self._kakaopage_category(cfg.get("KAKAOPAGE_CATEGORY"))
        params = urllib.parse.urlencode(
            {
                "keyword": query,
                "category_uid": category,
                "is_complete": "false",
                "sort_type": "ACCURACY",
                "page": 0,
                "size": self._int(cfg.get("MAX_RESULTS"), 20, 1, 100),
            }
        )
        url = f"https://bff-page.kakao.com/api/gateway/api/v2/search/series?{params}"
        data = self._get_json(url, cfg, headers=self._kakaopage_headers(cfg, query))
        items = ((data.get("result") or {}).get("list")) or []

        normalized_query = self._normalize(query)
        candidates = []
        max_results = self._int(cfg.get("MAX_RESULTS"), 20, 1, 100)
        seen_ids = set()
        for item in items:
            if self._is_kakaopage_adult(item) and not self._truthy(cfg.get("INCLUDE_ADULT")):
                continue
            series_id = item.get("series_id") or item.get("id")
            title = self._clean_text(item.get("title"))
            if not series_id or not self._matches_candidate_title(normalized_query, title, cfg):
                continue
            series_key = str(series_id)
            if series_key in seen_ids:
                continue
            seen_ids.add(series_key)
            candidates.append((item, series_id))
            if len(candidates) >= max_results:
                break

        details = self._parallel_map(
            lambda candidate: self._kakaopage_detail(candidate[1], cfg),
            candidates,
        )
        results = []
        for (item, series_id), detail in zip(candidates, details):
            merged = dict(item)
            merged.update(detail)
            title = self._clean_text(merged.get("title"))
            if not title or not series_id:
                continue
            about = merged.get("_about") or {}
            about_detail = about.get("detail") or {}
            category_list = about_detail.get("category_list") or []
            genre = self._clean_text(category_list) or self._clean_text(
                [merged.get("category"), merged.get("sub_category")]
            )
            tags = self._clean_text([
                keyword.get("title") if isinstance(keyword, dict) else keyword
                for keyword in (about.get("theme_keyword_list") or [])
            ])
            status = self._kakaopage_status(merged)
            completed = status.get("publication_status") == "2"
            results.append(
                self._item(
                    source="카카오페이지",
                    title=title,
                    author=self._clean_text(merged.get("authors")),
                    publisher=self._clean_text(about_detail.get("publisher_name")) or "카카오페이지",
                    cover=self._kakaopage_image(merged.get("thumbnail")),
                    description=about.get("description") or merged.get("description") or "",
                    link=f"https://page.kakao.com/content/{series_id}",
                    genre=genre,
                    tags=tags,
                    pub_date=merged.get("start_sale_dt") or merged.get("last_slide_added_dt") or "",
                    score=self._kakaopage_score(merged),
                    release_date=merged.get("start_sale_dt") or "",
                    publication_start_date=merged.get("start_sale_dt") or "",
                    publication_end_date=merged.get("last_slide_added_dt") if completed else "",
                    total_chapters=merged.get("on_sale_count") or 0,
                    books_lv={0: "everyone", 15: "ma15+", 19: "adult only"}.get(merged.get("age_grade"), ""),
                    **status,
                )
            )
        return results

    def _kakaopage_detail(self, series_id, cfg):
        base = "https://bff-page.kakao.com/api/gateway/api/v1/content/"
        query = urllib.parse.urlencode({"series_id": series_id})
        try:
            data = self._get_json(base + "overview?" + query, cfg, headers=self._kakaopage_headers(cfg, ""))
            content = (data.get("result") or {}).get("content") or {}
        except Exception:
            return {}
        try:
            about_data = self._get_json(base + "about?" + query, cfg, headers=self._kakaopage_headers(cfg, ""))
            content["_about"] = about_data.get("result") or {}
        except Exception:
            pass
        return content

    @staticmethod
    def _kakaopage_status(item):
        # KakaoPage's web client maps on_issue Y to ongoing and N to complete.
        status = str(item.get("on_issue") or "").upper()
        if status == "Y":
            return {"publication_status": "0"}
        if status == "N":
            return {"publication_status": "2"}
        return {}

    @staticmethod
    def _novelpia_status(item):
        # The public search page renders novel_live 2/4 as 연재중단.
        complete = str(item.get('is_complete', ''))
        if complete == '1':
            return {'publication_status': '2'}
        if complete == '0' and str(item.get('novel_live', '')) in {'2', '4'}:
            return {'publication_status': '1', 'publication_status_label': '연재중단'}
        return {'publication_status': '0'} if complete == '0' else {}

    def _search_novelpia(self, query, cfg):
        proxy_url = self._proxy_url(cfg)
        cooldown_key = ("novelpia", hashlib.sha256(proxy_url.encode("utf-8")).hexdigest())
        with self._cache_lock:
            cooldown_until = self._source_cooldowns.get(cooldown_key, 0)
        if cooldown_until > time.time():
            return []

        url = "https://novelpia.com/proc/novel?" + urllib.parse.urlencode({
            "cmd": "novel_search", "search_type": cfg.get("NOVELPIA_SEARCH_TYPE", "novel_name"), "search_val": query,
            "page": 1, "rows": self._int(cfg.get("MAX_RESULTS"), 30, 1, 100),
        })
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://novelpia.com/",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            body, response = self._request(
                url,
                cfg,
                headers=headers,
                method="GET",
                timeout=self._int(cfg.get("NOVELPIA_TIMEOUT"), 3, 1, 15),
            )
        except (TimeoutError, urllib.error.URLError) as e:
            with self._cache_lock:
                self._source_cooldowns[cooldown_key] = time.time() + NOVELPIA_FAILURE_COOLDOWN
            raise RuntimeError("request timed out; skipping Novelpia for 5 minutes") from e
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            text = body.decode("utf-8")
        except Exception:
            text = body.decode(charset, errors="replace")
        data = json.loads(text)
        if data.get("status") != 200:
            raise ValueError("Novelpia search failed: " + str(data.get("errmsg") or data.get("status")))
        novel_list = data.get("list") or []

        return self._novelpia_rows(novel_list, cfg)

    def _novelpia_rows(self, novel_list, cfg):
        results = []
        for item in novel_list:
            age = self._int(item.get("novel_age"), 0, 0, 99)
            if age >= 15 and not self._truthy(cfg.get("INCLUDE_ADULT")):
                continue
            novel_no = item.get("novel_no")
            title = self._clean_text(item.get("novel_name"))
            if not novel_no or not title:
                continue
            genres = item.get("novel_genre_arr")
            if not isinstance(genres, list):
                try:
                    genres = json.loads(item.get("novel_genre") or "[]")
                except (ValueError, TypeError):
                    genres = []
            if not isinstance(genres, list):
                genres = []
            thumb = item.get("cover_url") or item.get("novel_thumb_all") or item.get("novel_thumb") or ""
            if thumb and thumb.startswith("/"):
                thumb = urllib.parse.urljoin("https://images.novelpia.com" if thumb.startswith("/imagebox/") else "https://novelpia.com", thumb)
            results.append(
                self._item(
                    source="노벨피아",
                    title=title,
                    author=self._clean_text(item.get("writer_nick")),
                    publisher="노벨피아",
                    cover=thumb,
                    description=self._clean_text(item.get("novel_story")),
                    link=f"https://novelpia.com/novel/{novel_no}",
                    genre=self._clean_text(["웹소설", *[v for v in genres if v in NOVEL_GENRES]]),
                    tags=self._clean_text([v for v in genres if v not in NOVEL_GENRES]),
                    pub_date="",
                    score="",
                    isbn=item.get("isbn") or "",
                    author_id=str(item.get("mem_no") or ""),
                    **self._novelpia_status(item),
                    total_chapters=self._int(item.get("count_book"), 0, 0, 1000000),
                    publication_start_date=item.get("start_date") or "",
                    publication_end_date=(item.get("complete_date") or "") if item.get("is_complete") == 1 else "",
                    books_lv={0: "everyone", 15: "ma15+", 19: "adult only"}.get(item.get("novel_age"), ""),
                )
            )
        return results

    def _search_munpia(self, query, cfg):
        url = "https://www.munpia.com/api/v1/main/search?" + urllib.parse.urlencode({"query": query})
        data = self._get_json(url, cfg, headers=self._munpia_headers(cfg))
        items = ((data.get("result") or {}).get("searchNovelTabDtos")) or []

        candidates = []
        max_results = self._int(cfg.get("MAX_RESULTS"), 20, 1, 100)
        for item in items:
            if item.get("adult") and not self._truthy(cfg.get("INCLUDE_ADULT")):
                continue
            novel_id = item.get("novelId")
            title = self._clean_text(item.get("title"))
            if not novel_id or not title or not self._matches_candidate_title(query, title, cfg):
                continue
            candidates.append((item, novel_id))
            if len(candidates) >= max_results:
                break

        details = self._parallel_map(
            lambda candidate: self._munpia_detail(candidate[1], cfg),
            candidates,
        )
        results = []
        for (item, novel_id), detail in zip(candidates, details):
            merged = dict(item)
            merged.update(detail)
            title = self._clean_text(merged.get("title"))
            genres = merged.get("genres") or [merged.get("mainGenre"), merged.get("subGenre")]
            status = self._munpia_status(merged)
            completed = status.get("publication_status") == "2"
            results.append(
                self._item(
                    source="문피아",
                    title=title,
                    author=self._clean_text(merged.get("authorName") or merged.get("author")),
                    publisher="문피아",
                    cover=merged.get("coverUrl") or "",
                    description=merged.get("introduction") or merged.get("story") or "",
                    link=f"https://www.munpia.com/novel/detail/{novel_id}",
                    genre=self._clean_text(["웹소설", *genres]),
                    tags=self._join_names(merged.get("tags") or merged.get("tag") or []),
                    pub_date=self._clean_text(merged.get("createdAt") or merged.get("updateAt")),
                    score="",
                    isbn=self._clean_text(merged.get("isbn")) or f"munpia:{novel_id}",
                    release_date=merged.get("createdAt") or "",
                    publication_start_date=merged.get("createdAt") or "",
                    publication_end_date=merged.get("updatedAt") if completed else "",
                    total_chapters=self._int(merged.get("chapterCount") or merged.get("entryCount"), 0, 0, 1000000),
                    **status,
                )
            )
        return results

    def _munpia_detail(self, novel_id, cfg):
        url = f"https://www.munpia.com/api/v1/pc/novel-detail/{novel_id}"
        try:
            data = self._get_json(url, cfg, headers=self._munpia_headers(cfg))
            return ((data.get("result") or {}).get("novelInfo")) or {}
        except Exception:
            return {}

    def _munpia_status(self, item):
        if self._truthy(item.get("finish") if "finish" in item else item.get("finished")):
            return {"publication_status": "2"}
        if self._truthy(item.get("pause")):
            return {"publication_status": "1"}
        return {"publication_status": "0"}

    def _kakaopage_headers(self, cfg, query):
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://page.kakao.com",
            "Referer": "https://page.kakao.com/search/result?" + urllib.parse.urlencode({"keyword": query}),
            "User-Agent": (cfg.get("USER_AGENT") or DEFAULT_USER_AGENT) + " KakaoPageWeb/ssr",
        }
        if cfg.get("KAKAO_COOKIE"):
            headers["Cookie"] = cfg.get("KAKAO_COOKIE")
        return headers

    def _kakao_webtoon_headers(self, cfg, query):
        headers = {
            "Accept": "application/json",
            "Referer": "https://webtoon.kakao.com/search?" + urllib.parse.urlencode({"keyword": query}),
        }
        if cfg.get("KAKAO_COOKIE"):
            headers["Cookie"] = cfg.get("KAKAO_COOKIE")
        return headers

    def _munpia_headers(self, cfg):
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.munpia.com/",
        }
        if cfg.get("MUNPIA_COOKIE"):
            headers["Cookie"] = cfg.get("MUNPIA_COOKIE")
        return headers

    def _join_names(self, values):
        if not values:
            return ""
        names = []
        for value in values if isinstance(values, list) else [values]:
            if isinstance(value, dict):
                name = (
                    value.get("name")
                    or value.get("description")
                    or value.get("tagName")
                    or value.get("title")
                    or value.get("displayName")
                )
            else:
                name = value
            name = self._clean_text(name)
            if name and name not in names:
                names.append(name)
        return ", ".join(names)

    def _kakao_publisher(self, authors):
        if not isinstance(authors, list):
            return ""
        for author in authors:
            if isinstance(author, dict) and author.get("type") == "PUBLISHER":
                return self._clean_text(author.get("name"))
        return ""

    def _kakaopage_image(self, value):
        if not value:
            return ""
        if str(value).startswith("http"):
            return value
        return "https://page-images.kakaoentcdn.com/download/resource?" + urllib.parse.urlencode({"kid": value})

    def _kakaopage_category(self, value):
        value = (value or "all").strip().lower()
        return {"all": 0, "comic": 10, "webtoon": 10, "novel": 11, "book": 16}.get(value, self._int(value, 0, 0, 9999))

    def _kakaopage_score(self, item):
        prop = item.get("service_property") if isinstance(item, dict) else None
        if not isinstance(prop, dict):
            return ""
        count = prop.get("rating_count") or 0
        total = prop.get("rating_sum") or 0
        if count:
            return f"{round(float(total) / float(count), 2)}"
        return ""

    def _is_kakaopage_adult(self, item):
        return self._int(item.get("age_grade"), 0, 0, 99) >= 19

    def _truthy(self, value):
        return str(value).strip().lower() in ("1", "true", "yes", "y", "on")

    def _int(self, value, default, min_value, max_value):
        try:
            value = int(value)
        except Exception:
            value = default
        return max(min_value, min(max_value, value))

def search(source, query, matches, limit=8, content_kind=""):
    adapter = SearchAdapter(matches)
    cfg = {
        "MAX_RESULTS": max(30, limit), "NOVELPIA_TIMEOUT": 8,
        "KAKAOPAGE_CATEGORY": "webtoon" if content_kind == "manhwa" else "novel",
        "INCLUDE_ADULT": True,
    }
    rows = getattr(adapter, "_search_" + source)(query, cfg)
    result = []
    seen = set()
    for row in rows:
        if not matches(query, row.get("title", "")):
            continue
        url = row["url"]
        if url in seen:
            continue
        seen.add(url)
        row.update(id=source + ":" + url.rsplit("/", 1)[-1], source=source,
                   source_label=SOURCE_LABELS[source],
                   variant_label="웹툰" if (
                       source in ("naver_webtoon", "kakao_webtoon")
                       or source == "kakaopage" and content_kind == "manhwa"
                   ) else "소설")
        result.append(row)
    return result[:limit]


def _author_identity(value):
    return re.sub(r"\s+", "", html.unescape(str(value or ""))).casefold()


def search_novelpia_author(query, author, matches, search_url='', known_ids=None, limit=8):
    """Resolve profiles through public author search, then optional SearXNG.

    Search results only locate pages; author names are verified on Novelpia.
    No account cookies are used. Failed lookups are never cached here.
    """
    adapter = SearchAdapter(matches)
    cfg = {"INCLUDE_ADULT": True, "MAX_RESULTS": 100}
    ids = [str(value) for value in (known_ids or []) if str(value).isdigit()][:3] if author else []
    profiles = {value: author for value in ids}
    if not profiles and author:
        try:
            author_rows = adapter._search_novelpia(author, dict(cfg, NOVELPIA_SEARCH_TYPE='writer_nick'))
        except (OSError, ValueError, RuntimeError):
            author_rows = []
        profiles = {str(row['author_id']): author for row in author_rows
            if str(row.get('author_id') or '').isdigit()
            and _author_identity(row.get('author')) == _author_identity(author)}
    if not profiles:
        profiles = _discover_novelpia_profiles(adapter, query, author, matches, cfg, search_url)
    ids = list(profiles)[:3]
    result = []
    validated_ids = []
    seen = set()
    for author_id in ids:
        for page in range(1, 6):
            params = {'mode': 'get_member_writer_novel', 'mem_no': author_id,
                      'paging[rowCount]': 100, 'paging[curPage]': page,
                      'paging[order]': 'date', 'paging[sort][date]': 1}
            body, _ = adapter._request('https://novelpia.com/proc/user', cfg,
                method='POST', data=urllib.parse.urlencode(params).encode(),
                headers={'Referer': 'https://novelpia.com/user/' + author_id,
                         'Content-Type': 'application/x-www-form-urlencoded'})
            data = json.loads(body.decode('utf-8'))
            if str(data.get('status')) != '200':
                raise ValueError('노벨피아 작가 공개 작품 목록 조회 실패')
            items = (data.get('result') or {}).get('novel') or []
            if not isinstance(items, list):
                raise ValueError('노벨피아 작품 목록 형식 변경')
            own_items = [item for item in items if isinstance(item, dict)
                and str(item.get('mem_no')) == author_id
                and _author_identity(item.get('writer_nick')) == _author_identity(profiles[author_id])
                and str(item.get('is_del') or '0') == '0']
            if own_items and author_id not in validated_ids:
                validated_ids.append(author_id)
            for row in adapter._novelpia_rows(own_items, cfg):
                if matches(query, row['title']) and row['url'] not in seen:
                    seen.add(row['url'])
                    row.update(id='novelpia:' + row['url'].rsplit('/', 1)[-1], source='novelpia',
                        source_label='노벨피아', variant_label='소설')
                    result.append(row)
            if len(result) >= limit or len(items) < 100:
                break
    return result[:limit], validated_ids


def _discover_novelpia_profiles(adapter, query, author, matches, cfg, search_url=''):
    """Discover links without login; verify identities only on Novelpia pages."""
    searches = []
    if search_url:
        parsed = urllib.parse.urlsplit(search_url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError('잘못된 보조 검색 URL')
        params = dict(urllib.parse.parse_qsl(parsed.query))
        params.update(q='site:novelpia.com/novel/ ' + (author or query), format='json')
        searches.append(('json', urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(params)))))
    terms = [query + ' 노벨피아']
    if author:
        terms.append(author + ' 노벨피아')
    for term in terms:
        searches.append(('html', 'https://search.yahoo.co.jp/search?' + urllib.parse.urlencode({'p': term})))
        searches.append(('html', 'https://search.naver.com/search.naver?' + urllib.parse.urlencode({'where':'web', 'query':term})))
    seen = set()
    profiles = {}
    deadline = time.monotonic() + 20
    checked_pages = 0
    for kind, url in searches:
        if time.monotonic() >= deadline or checked_pages >= 8:
            break
        try:
            if kind == 'json':
                data = adapter._get_json(url, cfg)
                links = [item.get('url', '') for item in data.get('results', []) if isinstance(item, dict)]
            else:
                body, _ = adapter._request(url, cfg, timeout=max(1, min(5, deadline - time.monotonic())))
                text = html.unescape(body.decode('utf-8', errors='replace'))
                links = re.findall(r'https?://(?:www\.)?novelpia\.com/novel/\d+(?!\d)', text)
        except (OSError, ValueError, AttributeError):
            continue
        urls = []
        for link in links:
            match = re.fullmatch(r'https?://(?:www\.)?novelpia\.com/novel/(\d+)/?(?:[?#].*)?', str(link))
            if match:
                canonical = 'https://novelpia.com/novel/' + match.group(1)
                if canonical not in seen:
                    seen.add(canonical)
                    urls.append(canonical)
        for link in urls[:5]:
            if time.monotonic() >= deadline or checked_pages >= 8:
                break
            checked_pages += 1
            try:
                body, response = adapter._request(link, cfg, timeout=max(1, min(5, deadline - time.monotonic())))
                if urllib.parse.urlsplit(response.geturl()).hostname not in ('novelpia.com', 'www.novelpia.com'):
                    continue
                page = body.decode('utf-8', errors='replace')
                # Without a supplied author, require a matching work title
                # before accepting the page's author identity.
                if not author:
                    title = re.search(r'<title[^>]*>(.*?)</title>', page, re.I | re.S)
                    title = adapter._clean_text(title.group(1)) if title else ''
                    title = re.sub(r'^노벨피아\s*-\s*웹소설로 꿈꾸는 세상!\s*-\s*', '', title)
                    if not matches(query, title):
                        continue
                for attrs, label in re.findall(r'<a\b([^>]*)>(.*?)</a>', page, re.I | re.S):
                    if not re.search(r'class=["\'][^"\']*\bwriter-name\b', attrs, re.I):
                        continue
                    profile = re.search(r'href=["\'](?:https://novelpia\.com)?/user/(\d+)["\']', attrs, re.I)
                    name = adapter._clean_text(label)
                    if profile and name and (not author or _author_identity(name) == _author_identity(author)):
                        profiles[profile.group(1)] = name
                if profiles:
                    break
            except (OSError, ValueError):
                continue
        if profiles:
            break
    return profiles

"""Mirror only the validated, published Afterlight feed. No Site credentials needed."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

FEED_URL = "https://afterlight-ai-brief.alert-hare-7855.chatgpt.site/api/public-feed"
MAX_BYTES = 8 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def date(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "Invalid date")
    return dt.date.fromisoformat(value)


def text(value):
    require(isinstance(value, str) and bool(value.strip()), "Expected nonempty text")


def shape(value, required, optional=()):
    require(isinstance(value, dict), "Expected an object")
    require(set(required) <= value.keys() <= set(required) | set(optional), "Unexpected or missing public fields")


def timestamp(value):
    text(value)
    require(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None, "Timestamp requires timezone")


def validate(feed):
    shape(feed, ["version", "generatedAt", "editions", "weeks", "takes", "persistent"], ["papers"])
    require(feed["version"] == 1 and feed["persistent"] is True, "Persistent public feed required")
    timestamp(feed["generatedAt"])
    require(isinstance(feed["editions"], list) and feed["editions"], "Empty archive")
    require(isinstance(feed["weeks"], list) and isinstance(feed["takes"], dict), "Invalid review archive")
    edition_dates, stories = set(), set()
    for edition in feed["editions"]:
        shape(edition, ["date", "headline", "dek", "preview", "retrospective", "stories", "connections"], ["publishedAt", "updatedAt"])
        day = edition["date"]
        date(day)
        require(day not in edition_dates, "Duplicate edition")
        edition_dates.add(day)
        require(type(edition["preview"]) is bool and type(edition["retrospective"]) is bool, "Invalid publication flags")
        text(edition["headline"])
        text(edition["dek"])
        require(isinstance(edition["stories"], list) and len(edition["stories"]) <= 5, "Invalid story count")
        for story in edition["stories"]:
            shape(story, ["id", "title", "category", "company", "tags", "summary", "why", "facts", "context", "insight", "watch", "caveat", "sources"])
            require(isinstance(story["id"], str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", story["id"]), "Invalid story ID")
            key = (day, story["id"])
            require(key not in stories, "Duplicate story")
            stories.add(key)
            for name in ("title", "category", "company", "summary", "why", "context", "insight", "watch", "caveat"):
                text(story[name])
            for name in ("tags", "facts"):
                require(isinstance(story[name], list) and 1 <= len(story[name]) <= 5, "Invalid story list")
                for item in story[name]:
                    text(item)
            require(isinstance(story["sources"], list) and story["sources"], "Missing original sources")
            for source in story["sources"]:
                shape(source, ["label", "url", "publishedDate"])
                text(source["label"])
                date(source["publishedDate"])
                url = urllib.parse.urlsplit(source["url"])
                require(url.scheme in ("http", "https") and bool(url.netloc), "Invalid source URL")
        require(isinstance(edition["connections"], list), "Invalid daily connections")
        for connection in edition["connections"]:
            shape(connection, ["title", "description", "storyIds"])
            text(connection["title"])
            text(connection["description"])
            require(isinstance(connection["storyIds"], list) and all((day, sid) in stories for sid in connection["storyIds"]), "Unresolved daily reference")
    week_dates = set()
    for week in feed["weeks"]:
        shape(week, ["startDate", "endDate", "headline", "retrospective", "executiveSummary", "keyPoints", "shifts", "connections", "watchNext"], ["publishedAt", "updatedAt"])
        start, end = date(week["startDate"]), date(week["endDate"])
        require(start.weekday() == 0 and end - start == dt.timedelta(days=6), "Invalid weekly window")
        require(week["startDate"] not in week_dates, "Duplicate review")
        week_dates.add(week["startDate"])
        require(all((start + dt.timedelta(days=i)).isoformat() in edition_dates for i in range(7)), "Incomplete weekly coverage")
        text(week["headline"])
        text(week["executiveSummary"])
        require(type(week["retrospective"]) is bool, "Invalid review flag")
        require(isinstance(week["keyPoints"], list) and 1 <= len(week["keyPoints"]) <= 3, "Invalid key points")
        for point in week["keyPoints"]:
            text(point)
        require(isinstance(week["shifts"], list) and 1 <= len(week["shifts"]) <= 3, "Invalid ranked shifts")
        require(isinstance(week["connections"], list), "Invalid weekly connections")
        for item in week["shifts"] + week["connections"]:
            required = ["id", "title", "summary", "whyRank", "evidence", "implication", "caveat", "watch", "storyRefs"] if "id" in item else ["title", "description", "storyRefs"]
            shape(item, required)
            for key in required:
                if key != "storyRefs":
                    text(item[key])
            require(isinstance(item["storyRefs"], list) and item["storyRefs"], "Missing weekly references")
            for ref in item["storyRefs"]:
                shape(ref, ["date", "id"])
                require(start <= date(ref["date"]) <= end and (ref["date"], ref["id"]) in stories, "Unresolved weekly reference")
        require(isinstance(week["watchNext"], list) and 1 <= len(week["watchNext"]) <= 3, "Invalid watch list")
        for item in week["watchNext"]:
            shape(item, ["question", "signal"])
            text(item["question"])
            text(item["signal"])
    for week, take in feed["takes"].items():
        require(week in week_dates, "Take without published review")
        shape(take, ["title", "body", "publishedAt"])
        require(isinstance(take["title"], str) and isinstance(take["body"], str), "Invalid published take")
        timestamp(take["publishedAt"])
    validate_papers(feed.get("papers", []))
    return edition_dates, week_dates


def validate_papers(papers):
    require(isinstance(papers, list), "Invalid paper archive")
    days, identities = set(), set()
    for paper in papers:
        shape(paper, ["date", "paperId", "title", "headline", "authors", "publicationDate", "venue", "status", "category", "summary", "problem", "method", "results", "limitations", "implications", "sources"], ["revisionDate", "publishedAt", "updatedAt"])
        day, published = date(paper["date"]), date(paper["publicationDate"])
        require(published <= day, "Paper postdates its selection")
        if "revisionDate" in paper:
            require(published <= date(paper["revisionDate"]) <= day, "Invalid revision date")
        identity = paper["paperId"]
        require(isinstance(identity, str) and re.fullmatch(r"(?:arxiv:\d{4}\.\d{4,5}|doi:10\.\d{4,9}/\S+|url:https://\S+)", identity), "Invalid canonical paper ID")
        require(day not in days and identity not in identities, "Duplicate paper or selection day")
        days.add(day)
        identities.add(identity)
        require(paper["status"] in ("Preprint", "Peer-reviewed", "Technical report"), "Invalid review status")
        require(paper["category"] in ("Models & architectures", "AI systems", "Training & inference optimization", "AI chip architecture"), "Invalid research area")
        for key in ("title", "headline", "authors", "venue", "summary", "problem", "limitations", "implications"):
            text(paper[key])
        for key in ("publishedAt", "updatedAt"):
            if key in paper:
                timestamp(paper[key])
        for key, fields in (("method", ["title", "description"]), ("results", ["finding", "setup"])):
            require(isinstance(paper[key], list) and 1 <= len(paper[key]) <= 3, "Invalid paper sections")
            for item in paper[key]:
                shape(item, fields)
                for field in fields:
                    text(item[field])
        require(isinstance(paper["sources"], list) and 1 <= len(paper["sources"]) <= 5, "Missing paper sources")
        canonical_link = not identity.startswith("arxiv:")
        for source in paper["sources"]:
            shape(source, ["label", "url"])
            text(source["label"])
            url = urllib.parse.urlsplit(source["url"])
            require(url.scheme == "https" and bool(url.netloc), "Invalid paper URL")
            if identity.startswith("arxiv:") and url.hostname == "arxiv.org" and re.fullmatch(r"/(abs|pdf|html)/" + re.escape(identity[6:]) + r"(v[0-9]+)?(\.pdf)?/?", url.path):
                canonical_link = True
        require(canonical_link, "Missing canonical paper link")


def fetch_feed():
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(3):
        try:
            request = urllib.request.Request(FEED_URL, headers={"Accept": "application/json", "User-Agent": "Afterlight-GitHub-Sync/1.0"})
            with opener.open(request, timeout=25) as response:
                require(response.headers.get_content_type() == "application/json", "Expected JSON response")
                payload = response.read(MAX_BYTES + 1)
                require(len(payload) <= MAX_BYTES, "Feed exceeds size limit")
                return json.loads(payload)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(3 * (attempt + 1))


def mirror(destination, incoming):
    new_dates, new_weeks = validate(incoming)
    previous = json.loads(destination.read_text())
    old_dates, old_weeks = validate(previous)
    require(old_dates <= new_dates and old_weeks <= new_weeks, "Incoming feed would remove published history")
    paper_keys = lambda feed: {(p["date"], p["paperId"]) for p in feed.get("papers", [])}
    require(paper_keys(previous) <= paper_keys(incoming), "Incoming feed would remove published papers")
    comparable = lambda feed: {key: value for key, value in feed.items() if key != "generatedAt"}
    if comparable(previous) == comparable(incoming):
        return False
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(incoming, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(destination)
    return True


def package(destination):
    require(not destination.exists(), "Package directory already exists")
    destination.mkdir(parents=True)
    tracked = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    for name in tracked:
        path = Path(name)
        if not name or path.parts[0] in (".github", "scripts", "tests"):
            continue
        if name not in (".nojekyll", "feed.json") and path.suffix not in (".html", ".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2"):
            continue
        require(not path.is_symlink() and path.is_file(), "Only regular site files may be packaged")
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    require((destination / "index.html").is_file() and (destination / "feed.json").is_file(), "Missing site entry files")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path)
    args = parser.parse_args()
    if args.package:
        package(args.package)
        return
    incoming = fetch_feed()
    changed = mirror(Path("feed.json"), incoming)
    summary = f"{len(incoming['editions'])} daily editions; {len(incoming['weeks'])} weekly reviews; {len(incoming.get('papers', []))} paper briefs; snapshot {'updated' if changed else 'unchanged'}."
    print(summary)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(summary + "\n")


if __name__ == "__main__":
    main()

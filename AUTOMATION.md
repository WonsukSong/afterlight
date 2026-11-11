# Publication and GitHub synchronization

The existing Afterlight tasks publish an English daily briefing at 00:30 America/Los_Angeles and a weekly review on Mondays at 00:45. They write to the persistent Afterlight service; the website also reads that public feed directly.

The **Sync news and publish Pages** GitHub Actions workflow checks the public feed at minute 17 of each hour (UTC), on pushes to main, and when run manually. GitHub schedules can be delayed. It validates the complete archive, updates `feed.json` only when content changes, commits using the repository's short-lived built-in GitHub token, and explicitly deploys a Pages artifact. No personal access token or Site writer credential is required.

Every successful check republishes the validated snapshot, including unchanged content. This intentionally retries publication after an earlier deployment failure even if that earlier run already committed its update. Metadata timestamp changes alone do not create commits. Commits made with GITHUB_TOKEN do not trigger another push workflow, preventing loops.

Pages must use **Settings → Pages → Source: GitHub Actions**. The sync job can write repository content; the separate deploy job can publish Pages and request its deployment identity token. Only published news and published Editor's Takes are mirrored. Drafts and credentials are rejected by the public-data validator.

Network requests have 25-second timeouts and at most two transient retries. Invalid JSON, a nonpersistent fallback, broken references, missing historical dates, or a rejected Git push stop the run before deployment. Existing published content is preserved on failure. A later scheduled run retries. Check the Actions run result for failures; a stored schedule alone does not prove successful publication.

Public repositories may have scheduled workflows disabled after 60 days without repository activity. Ongoing news commits normally provide activity. If publication stops for an extended period, check the workflow's enabled status before resuming.

The workflow packages static site files only. Source code changes still require a new frontend build; daily news changes do not rebuild the application.

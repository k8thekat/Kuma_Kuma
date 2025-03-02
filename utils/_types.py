from typing import TypedDict


class GitHubIssueSubmissionResponse(TypedDict):
    id: int
    node_id: str
    url: str
    repository_url: str
    labels_url: str
    comments_url: str
    events_url: str
    html_url: str
    number: int
    state: str
    title: str
    body: str
    user: dict[str, str | int | bool]
    labels: list[dict[str, str | int | bool]]
    assignee: dict[str, str | int | bool]
    assignees: dict[str, str | int | bool]
    milestone: dict[str, str | int | bool]
    locked: bool
    active_lock_reason: str
    comments: int
    pull_request: dict
    closed_at: str | None  # isoformat datetime
    created_at: str | None  # isoformat datetime
    updated_at: str | None  # isoformat datetime
    closed_by: str | None  # isoformat datetime
    author_association: str
    state_reason: str

# ds01-jobs

Job submission service for the DS01 compute cluster.

## Admin CLI

Manage researcher API keys with `ds01-job-admin`.

### Prerequisites

- **GitHub CLI (`gh`)** - used to verify org membership. Install: https://cli.github.com/
- Authenticate with `gh auth login` before using key commands.
- Alternatively, set the `GITHUB_TOKEN` environment variable.

### Commands

```bash
# Create a key (username = GitHub username, must be an org member)
uv run ds01-job-admin key-create <github-username>

# List all keys
uv run ds01-job-admin key-list

# Revoke a key
uv run ds01-job-admin key-revoke <github-username>

# Rotate a key (revoke + create)
uv run ds01-job-admin key-rotate <github-username>
```

The `<github-username>` argument is the researcher's GitHub username, not an
arbitrary identifier. Org membership is checked against `hertie-data-science-lab`
via the GitHub API.

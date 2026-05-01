---
name: gws-drive
description: "Google Drive: Manage files, folders, and shared drives."
metadata:
  version: 0.22.5
  openclaw:
    category: "productivity"
    requires:
      bins:
        - gws
    cliHelp: "gws drive --help"
---

# drive (v3)

> **PREREQUISITE:** Read `../gws-shared/SKILL.md` for auth, global flags, and security rules. If missing, run `gws generate-skills` to create it.

```bash
gws drive <resource> <method> [flags]
```

## Helper Commands

| Command | Description |
|---------|-------------|
| [`+upload`](../gws-drive-upload/SKILL.md) | Upload a file with automatic metadata |

## API Resources

### about

  - `get` — Gets information about the user, the user's Drive, and system capabilities.

### accessproposals

  - `get` — Retrieves an access proposal by ID.
  - `list` — List the access proposals on a file.
  - `resolve` — Approves or denies an access proposal.

### apps

  - `get` — Gets a specific app.
  - `list` — Lists a user's installed apps.

### changes

  - `getStartPageToken` — Gets the starting pageToken for listing future changes.
  - `list` — Lists the changes for a user or shared drive.
  - `watch` — Subscribes to changes for a user.

### channels

  - `stop` — Stops watching resources through this channel.

### comments

  - `create` — Creates a comment on a file.
  - `delete` — Deletes a comment.
  - `get` — Gets a comment by ID.
  - `list` — Lists a file's comments.
  - `update` — Updates a comment with patch semantics.

### drives

  - `create` — Creates a shared drive.
  - `get` — Gets a shared drive's metadata by ID.
  - `hide` — Hides a shared drive from the default view.
  - `list` — Lists the user's shared drives.
  - `unhide` — Restores a shared drive to the default view.
  - `update` — Updates the metadata for a shared drive.

### files

  - `copy` — Creates a copy of a file and applies any requested updates with patch semantics.
  - `create` — Creates a file. Supports upload via multipart. Max file size: 5,120 GB.
  - `download` — Downloads the content of a file.
  - `export` — Exports a Google Workspace document to the requested MIME type. Limited to 10 MB.
  - `generateIds` — Generates a set of file IDs for use in create or copy requests.
  - `get` — Gets a file's metadata or content by ID. Use `alt=media` to download content.
  - `list` — Lists the user's files. Supports `q` parameter for search queries.
  - `listLabels` — Lists the labels on a file.
  - `modifyLabels` — Modifies the set of labels applied to a file.
  - `update` — Updates a file's metadata, content, or both. Supports patch semantics.
  - `watch` — Subscribes to changes to a file.

### operations

  - `get` — Gets the latest state of a long-running operation.

### permissions

  - `create` — Creates a permission for a file or shared drive.
  - `delete` — Deletes a permission.
  - `get` — Gets a permission by ID.
  - `list` — Lists a file's or shared drive's permissions.
  - `update` — Updates a permission with patch semantics.

### replies

  - `create` — Creates a reply to a comment.
  - `delete` — Deletes a reply.
  - `get` — Gets a reply by ID.
  - `list` — Lists a comment's replies.
  - `update` — Updates a reply with patch semantics.

### revisions

  - `delete` — Permanently deletes a file version (binary content only).
  - `get` — Gets a revision's metadata or content by ID.
  - `list` — Lists a file's revisions.
  - `update` — Updates a revision with patch semantics.

## Discovering Commands

Before calling any API method, inspect it:

```bash
# Browse resources and methods
gws drive --help

# Inspect a method's required params, types, and defaults
gws schema drive.<resource>.<method>
```

Use `gws schema` output to build your `--params` and `--json` flags.

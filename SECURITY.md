# Security Policy

## Supported version

Security fixes are applied to the latest release on the default branch.

## Deployment checklist

- Generate a unique `DIARY_ADMIN_KEY` with at least 32 random characters.
- Treat every participant diary URL and MCP URL as a password-bearing link.
- Use HTTPS for every non-local deployment.
- Keep the database on private, persistent storage and back it up regularly.
- Keep reverse-proxy access logs from recording protected URL paths.
- Rotate a participant key immediately if its URL is exposed.
- Do not use challenge entries for highly sensitive secrets; the challenge is an interactive privacy feature, not end-to-end encryption.

## Reporting a vulnerability

Please avoid posting access keys, diary contents, or working exploit details in a public issue. Open a minimal issue requesting a private contact channel, or contact the repository owner through their GitHub profile.

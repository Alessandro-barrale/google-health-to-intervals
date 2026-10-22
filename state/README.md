# state/

The sync workflows keep the Google refresh token here, in `token.enc`.

- The file is encrypted (AES-256-GCM) with a key derived from your
  `GOOGLE_CLIENT_SECRET` and `INTERVALS_API_KEY` secrets. Without both
  secrets it cannot be read.
- No health data is ever stored in this repository.
- Keep the repository **private** anyway.
- If you delete the file, just authorize again (Actions > Re-authorize).

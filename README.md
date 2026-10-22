# google-health-to-intervals

Copy your **Google Health** data (Fitbit, Pixel Watch, Fitbit Air) to
**[intervals.icu](https://intervals.icu)** automatically, a few times a day.

It is made for people who cannot use the Android apps that do this through
Health Connect, especially **iPhone users**. It runs **for free on your own
GitHub account** (GitHub Actions): no Raspberry Pi, no server, no app to install.

| Google Health | intervals.icu wellness field |
|---|---|
| Sleep duration (time asleep, naps excluded) | `sleepSecs` |
| Resting heart rate | `restingHR` |
| Heart rate variability (nightly rMSSD) | `hrv` |
| Weight (last weigh-in of the day) | `weight` |
| SpO2 (daily average) | `spO2` |
| Steps | `steps` |
| VO2 max (cardio fitness) | `vo2max` |
| Breathing rate | `respiration` |

The Google Health API does not provide the Fitbit *sleep score*, so
`sleepScore` stays empty. Empty values are never sent, so they never erase what
you already have in intervals.icu.

> **Status: early version.** The Google Health API replaced the Fitbit Web API
> in 2026. This tool follows Google's documentation, but some field names have
> not been checked against real data yet. If a value looks wrong, please open an
> issue.

## How it works

```
Google Health API  --->  your private GitHub repository (GitHub Actions)  --->  intervals.icu
```

- You create your **own** Google Cloud project, in "Testing" mode. Google does
  not need to review it because only you use it.
- The price of "Testing" mode: **Google asks you to approve access again every
  7 days.** This tool turns that into a one-minute step, and GitHub emails you
  the day before it is needed.
- Your data goes straight from Google to intervals.icu. There is no server of ours.

## Setup (about 20-30 minutes, once)

You need: the Google account that has your Fitbit / Pixel Watch data, an
intervals.icu account, and a free GitHub account.

### 1. Create your copy of this repository (PRIVATE)

1. Sign in to GitHub and open this repository's page.
2. Click the green **Use this template** button, then **Create a new repository**.
3. Choose a name (for example `my-health-sync`).
4. Select **Private**. This matters: keep your copy private.
5. Click **Create repository**.

### 2. Create a Google Cloud project

1. Open [console.cloud.google.com](https://console.cloud.google.com/) with the
   Google account that has your health data. Accept the terms if asked.
2. At the top, click the project selector, then **New project**. Name it
   `health-sync` and click **Create**. Make sure the new project is selected.

This tool uses only the Google Health API; it does not need any paid Google Cloud product.

### 3. Enable the Google Health API

1. Open [the Google Health API page](https://console.cloud.google.com/apis/library/health.googleapis.com)
   (or search for "Google Health API" in **APIs & Services > Library**).
2. Click **Enable**.

### 4. Set up the consent screen ("Testing", with yourself as test user)

1. Open **Google Auth Platform** (search for "OAuth consent screen" at the top
   of the console) and click **Get started**.
2. **App information**: app name `health-sync`, support email = your email.
3. **Audience**: choose **External**.
4. **Contact information**: your email. Accept the policy and click **Create**.
5. Go to **Audience**. Leave the publishing status on **Testing**.
   Under **Test users** click **Add users**, type your own Google email
   (the one with the health data) and click **Save**.
6. Go to **Data Access** and click **Add or remove scopes**. Type `googlehealth`
   in the filter and tick these three, then click **Update** and **Save**:
   - `.../auth/googlehealth.sleep.readonly`
   - `.../auth/googlehealth.health_metrics_and_measurements.readonly`
   - `.../auth/googlehealth.activity_and_fitness.readonly`

### 5. Create the OAuth client ("Desktop app")

1. In **Google Auth Platform** open **Clients** and click **Create client**.
2. **Application type: Desktop app**. Name: `health-sync`. Click **Create**.
3. Copy the **Client ID** and the **Client secret** somewhere safe for the next
   step. (You can see them again later by opening the client.)

### 6. Get your intervals.icu API key

1. In intervals.icu open **Settings**, scroll to **Developer Settings**.
2. Click to create an **API key** and copy it.
3. Note your **Athlete ID** shown in the same place (it looks like `i123456`).

### 7. Add the 4 secrets to your GitHub repository

In your new repository: **Settings > Secrets and variables > Actions >
New repository secret**. Add these four, one at a time:

| Name | Value |
|---|---|
| `GOOGLE_CLIENT_ID` | the Client ID from step 5 |
| `GOOGLE_CLIENT_SECRET` | the Client secret from step 5 |
| `INTERVALS_API_KEY` | the API key from step 6 |
| `INTERVALS_ATHLETE_ID` | your athlete ID from step 6 (e.g. `i123456`) |

Recommended: in the **Variables** tab (same page) add `GHTI_TIMEZONE` with your
time zone, for example `Europe/Rome` or `America/New_York`. Without it "today"
is computed in UTC.

### 8. Authorize Google (first time)

1. Open the **Actions** tab of your repository. If GitHub asks, click
   **I understand my workflows, go ahead and enable them**.
2. Click **Re-authorize** on the left, then **Run workflow**, leave the box
   empty and click the green **Run workflow** button.
3. Open the run that appears (refresh the page after a few seconds). In its
   **Summary** you will find the link **Authorize Google Health**. Open it.
4. Choose your Google account. Google says *"Google hasn't verified this app"*:
   it is your own app, so click **Continue**. Tick **all** the boxes and click
   **Continue**.
5. Your browser ends on an error page such as *"This site can't be reached"*.
   **That is expected.** Copy the **whole address** from the address bar. It
   starts with `http://127.0.0.1:8765/?state=`.
6. Within a few minutes, go back to **Actions > Re-authorize > Run workflow**,
   paste the address into **redirect_url** and click **Run workflow**.

The run saves the authorization (encrypted, in `state/token.enc`) and
immediately copies the last 14 days.

### 9. Check that it works

- The Re-authorize run should be green, and its Summary shows a table of the
  days copied.
- Open intervals.icu: the wellness values appear on the calendar and in the
  Fitness / Wellness charts.
- From now on the **Sync** workflow runs four times a day by itself. You can
  also start it by hand: **Actions > Sync > Run workflow**.

To copy older history: **Actions > Sync > Run workflow** and fill **from** with
the first day you want (for example `2025-01-01`).

## Weekly re-authorization

Google expires the authorization of "Testing" apps 7 days after you approve it.

- **Day 6**: the **Reminder** workflow turns red on purpose, so GitHub sends you
  an email. Its Summary says *"the Google authorization expires tomorrow"* and
  contains the link.
- Renewing takes a minute: open the link, approve, copy the address, run
  **Re-authorize** with it (steps 8.3 to 8.6).
- If you miss it, **Sync** fails with a clear message and the same link, and
  GitHub emails you again. Nothing is lost: after you re-authorize, the missed
  days (up to 14 by default) are copied automatically.

Don't want the red reminder run? Add the repository variable
`GHTI_REMINDER_EMAIL` = `false`. The reminder then only appears in the run Summary.

## Privacy

- Your health data travels **only from Google to intervals.icu**, inside
  GitHub's runner that works for you. There is **no server of ours** and no
  analytics.
- No health data is stored in your repository. The run Summary shows which
  fields were copied for each day, not the values.
- The Google authorization is stored encrypted (AES-256-GCM) in
  `state/token.enc`, with a key derived from your `GOOGLE_CLIENT_SECRET` and
  `INTERVALS_API_KEY` secrets. Your secrets stay in GitHub's secret store.
- Access is read-only: the tool cannot change or delete anything in Google Health.
- You can revoke access at any time at
  [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

## Limits

- **Re-authorization every 7 days**, because the Google project is in "Testing"
  mode. This is Google's rule for apps that have not gone through verification.
- A "Testing" project accepts at most **100 test users**. That is why every user
  creates their own project.
- It depends on the **Google Health API**: if Google changes the API or its
  rules, the tool may need an update.
- Data is copied 4 times a day, not in real time. Today's steps are completed by
  the later runs.
- GitHub Actions is free for private repositories within the monthly minutes of
  the free plan; this tool uses a small part of them (about 5 short runs a day).
- intervals.icu allows 5,000 API requests a day; a normal sync uses one.

## FAQ

**Do I need Fitbit Premium?**
The tool copies whatever the Google Health API returns for your account. Which
metrics a device or subscription provides depends on Google, not on this tool.

**Does it work with an iPhone?**
Yes. Nothing runs on your phone: the data is read from Google's cloud.

**Why does the browser show an error during authorization?**
The OAuth client is a "Desktop app", so Google sends you back to
`127.0.0.1` (your own computer), where nothing is listening. The code you need
is in the address. This replaces Google's old copy-paste flow, which was shut down.

**Can I run it on my computer instead of GitHub?**
Yes, see *Run on your computer* below.

**I changed my intervals.icu API key or Google client secret. Now what?**
Update the secret on GitHub and authorize again: the stored token is encrypted
with those secrets.

**Some values are missing.**
Check that you ticked all the boxes on Google's consent screen (the Summary
lists anything not authorized), and that your device records that value.
If the Summary shows a "Problems" section, please open an issue with that text
(it contains no health data).

**Can I make my copy public?**
Please don't. The token is encrypted, but a private repository is the safe choice.

## Run on your computer

Install [uv](https://docs.astral.sh/uv/), download this repository, then put the
same four values either in environment variables or in a file named
`config.env` in your configuration folder (`%APPDATA%\ghti` on Windows,
`~/Library/Application Support/ghti` on macOS, `~/.config/ghti` on Linux):

```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
INTERVALS_API_KEY=...
INTERVALS_ATHLETE_ID=i123456
GHTI_TIMEZONE=Europe/Rome
```

Commands:

```
uv run ghti auth-url                      # print the consent link
uv run ghti auth-finish "<address>"       # save the authorization
uv run ghti sync                          # copy the last 3 days (--days N to change)
uv run ghti sync --dry-run --show-values  # look without writing
uv run ghti backfill --from 2025-01-01    # copy older history
uv run ghti export-csv -o wellness.csv    # last 30 days as CSV (--from/--to)
uv run ghti status                        # when does the authorization expire?
```

On a computer the encrypted token is saved in the same configuration folder.

## Development

```
uv run pytest -q
```

The tests are offline: every HTTP call is faked. The mapping between Google's
fields and intervals.icu lives in one file, `src/ghti/mapping.py`; the places
not yet checked against real data are marked `DA VERIFICARE CON DATI REALI`.

## Support this project

If this saves you time, you can support its development here:
**[DONATION LINK PLACEHOLDER]**

## License

MIT, see [LICENSE](LICENSE). Not affiliated with Google, Fitbit or intervals.icu.

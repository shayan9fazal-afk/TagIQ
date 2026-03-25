# Hosting the UI on GitHub Pages

This repository now includes a **static frontend**:

- `index.html`
- `styles.css`
- `app.js`

It calls your Python backend endpoint: `POST /api/analyze`.

## Option A (recommended): GitHub Pages for UI + VPS/PC for backend

### 1) Push these files to a GitHub repo

Include at least:

- `index.html`
- `styles.css`
- `app.js`

### 2) Enable GitHub Pages

- Repo → **Settings** → **Pages**
- Source: **Deploy from a branch**
- Branch: `main` (or `master`) and root `/`
- Save

Your site will be available at:

- `https://<username>.github.io/<repo>/`

### 3) Point the UI to your backend

Open the website, expand **Advanced settings**, and set:

- **Backend API base URL**: e.g. `https://your-backend-domain.com`

This value is saved in the browser (localStorage).

## Backend hosting notes

GitHub Pages cannot run Python. You must run the backend elsewhere:

- A small VPS (recommended)
- Your PC (for personal use)

If you host the backend on a different domain, you may need to enable CORS on the backend.


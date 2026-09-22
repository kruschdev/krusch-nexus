# 🌐 Google Workspace Connection & Setup Guide

This guide explains how to connect your **Google Workspace (Gmail, Google Drive, Google Docs, Sheets, Slides)** account to Krusch-Nexus for automated document ingestion and institutional knowledge synchronization.

---

## 🚀 Option A: User OAuth2 Connection (Recommended for Individuals & Small Teams)

### Step 1: Create a Google Cloud Credentials App
1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project named **Krusch-Nexus-Sync**.
3. In the left menu, go to **APIs & Services > Library**:
   - Search for **Google Drive API** and click **Enable**.
   - Search for **Gmail API** and click **Enable**.
4. Go to **APIs & Services > OAuth Consent Screen**:
   - Select **External** (or **Internal** if using Google Workspace domain).
   - Enter your App Name (*Krusch-Nexus*) and user support email.
   - Add Scopes: `https://www.googleapis.com/auth/drive.readonly` and `https://www.googleapis.com/auth/gmail.readonly`.
5. Go to **APIs & Services > Credentials**:
   - Click **Create Credentials > OAuth client ID**.
   - Select Application Type: **Web Application**.
   - Authorized Redirect URI: `http://10.0.0.85:8001/api/auth/google/callback` (or your domain URL).
   - Click **Create** and copy your **Client ID** and **Client Secret**.

### Step 2: Connect in Krusch-Nexus Web Portal
1. Open the Krusch-Nexus Data Ingestion Portal (`http://10.0.0.85:8001/chat`).
2. Click **🔑 Connect Google Workspace**.
3. Paste your **Client ID** and **Client Secret**.
4. Click **Connect & Sync**. Your Google Drive files and Gmail threads will begin indexing automatically!

---

## 🏢 Option B: Admin Service Account Setup (Recommended for Enterprise IT Admins)

For domain-wide automated ingestion across all employee Google accounts:

1. In [Google Cloud Console](https://console.cloud.google.com/), go to **APIs & Services > Credentials**.
2. Click **Create Credentials > Service Account**.
3. Name it `nexus-sync-service-account` and click **Create and Continue**.
4. Click the created Service Account, go to **Keys > Add Key > Create New Key (JSON)**, and download the `.json` file.
5. In **Google Workspace Admin Console** (`admin.google.com`):
   - Go to **Security > Access and data control > API controls > Manage Domain Wide Delegation**.
   - Add your Service Account Client ID.
   - Grant Scopes:
     `https://www.googleapis.com/auth/drive.readonly`
     `https://www.googleapis.com/auth/gmail.readonly`
     `https://www.googleapis.com/auth/admin.directory.user.readonly`
6. Upload the Service Account JSON file in the Krusch-Nexus Web Portal under **🔑 Connect Google Workspace > Admin Service Account**.

---

## 🤖 Triggering Sync via MCP Server (For AI Agents)

Once connected, your AI assistant (Claude Desktop, Cursor, OpenClaw) can trigger synchronization at any time by calling the MCP tool:

```json
nexus_sync_google_workspace(
  "workspace_name": "General",
  "sync_type": "all"
)
```

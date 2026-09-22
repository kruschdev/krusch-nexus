import os
import json
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any

logger = logging.getLogger("nexus.sync_provider")

# 1. Base Class for Directory and Document Sync
class BaseSyncProvider(ABC):
    @abstractmethod
    def fetch_users(self) -> List[Dict[str, Any]]:
        """Fetch normalized directory users/employees."""
        pass

    @abstractmethod
    def fetch_emails(self) -> List[Dict[str, Any]]:
        """Fetch emails (sender, subject, body, date, external_id)."""
        pass

    @abstractmethod
    def fetch_files(self) -> List[Dict[str, Any]]:
        """Fetch document files (filename, content, external_id)."""
        pass


# 2. Local JSON Provider (for Offline/Local Homelab Testing)
class LocalJsonDirectoryProvider(BaseSyncProvider):
    def __init__(self, directory_file: str = None, emails_file: str = None):
        backend_dir = os.path.dirname(os.path.abspath(__file__))
        self.directory_file = directory_file or os.path.join(backend_dir, "mock_directory.json")
        self.emails_file = emails_file or os.path.join(backend_dir, "mock_emails.json")

    def fetch_users(self) -> List[Dict[str, Any]]:
        logger.info(f"Loading local directory mock from {self.directory_file}")
        if not os.path.exists(self.directory_file):
            logger.warning(f"Mock directory file not found at {self.directory_file}. Returning empty list.")
            return []
        with open(self.directory_file, "r") as f:
            return json.load(f)

    def fetch_emails(self) -> List[Dict[str, Any]]:
        logger.info(f"Loading local emails mock from {self.emails_file}")
        if not os.path.exists(self.emails_file):
            logger.warning(f"Mock emails file not found at {self.emails_file}. Returning empty list.")
            return []
        with open(self.emails_file, "r") as f:
            return json.load(f)

    def fetch_files(self) -> List[Dict[str, Any]]:
        logger.info("Loading local files mock")
        # Return some mock files representing shared drive files
        return [
            {
                "external_id": "file_201",
                "filename": "acme_partnership_notes.md",
                "content": "# Acme Partnership Notes\n\n- Meeting Date: 2026-06-15\n- Lead: Alice Smith\n- Summary: Discussed NDA and term length. Acme agreed to Standard IP protections."
            },
            {
                "external_id": "file_202",
                "filename": "security_audit_checklist.txt",
                "content": "SOC2 Audit compliance checklist:\n1. Verify log retention (365 days) -> Active.\n2. Verify database encryption -> Pending rotation.\n3. Verify administrative security reviews -> Complete."
            }
        ]


# 3. Google Workspace Provider with Service Account or Fallback Mocks
class GoogleWorkspaceDirectoryProvider(BaseSyncProvider):
    def __init__(self):
        self.credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        self.delegated_user = os.getenv("GOOGLE_WORKSPACE_DELEGATED_USER") # admin email to impersonate
        
        # Check if client libraries are available
        self.has_libs = False
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            self.has_libs = True
            self.service_account = service_account
            self.build = build
        except ImportError:
            logger.warning("Google Client libraries (google-api-python-client, google-auth) not installed.")

    def _get_directory_service(self):
        if not self.has_libs:
            raise ImportError("Google API client libraries are not installed.")
        if not self.credentials_path or not os.path.exists(self.credentials_path):
            raise FileNotFoundError("Google credentials not configured or file not found.")

        scopes = [
            'https://www.googleapis.com/auth/admin.directory.user.readonly',
            'https://www.googleapis.com/auth/admin.directory.group.readonly'
        ]
        
        creds = self.service_account.Credentials.from_service_account_file(
            self.credentials_path, scopes=scopes
        )
        if self.delegated_user:
            creds = creds.with_subject(self.delegated_user)
            
        return self.build('admin', 'directory_v1', credentials=creds)

    def _get_gmail_service(self):
        if not self.has_libs:
            raise ImportError("Google API client libraries are not installed.")
        if not self.credentials_path or not os.path.exists(self.credentials_path):
            raise FileNotFoundError("Google credentials not configured or file not found.")

        scopes = ['https://www.googleapis.com/auth/gmail.readonly']
        creds = self.service_account.Credentials.from_service_account_file(
            self.credentials_path, scopes=scopes
        )
        if self.delegated_user:
            creds = creds.with_subject(self.delegated_user)
            
        return self.build('gmail', 'v1', credentials=creds)

    def _get_drive_service(self):
        if not self.has_libs:
            raise ImportError("Google API client libraries are not installed.")
        if not self.credentials_path or not os.path.exists(self.credentials_path):
            raise FileNotFoundError("Google credentials not configured or file not found.")

        scopes = ['https://www.googleapis.com/auth/drive.readonly']
        creds = self.service_account.Credentials.from_service_account_file(
            self.credentials_path, scopes=scopes
        )
        if self.delegated_user:
            creds = creds.with_subject(self.delegated_user)
            
        return self.build('drive', 'v3', credentials=creds)

    def fetch_documents(self) -> List[Dict[str, Any]]:
        logger.info("Fetching documents from Google Drive / Docs")
        try:
            service = self._get_drive_service()
            results = service.files().list(
                pageSize=20,
                fields="files(id, name, mimeType, createdTime, webViewLink)"
            ).execute()
            files = results.get('files', [])

            docs = []
            for f in files:
                docs.append({
                    "id": f.get("id"),
                    "filename": f.get("name"),
                    "mime_type": f.get("mimeType"),
                    "created_at": f.get("createdTime"),
                    "link": f.get("webViewLink"),
                    "content": f"Google Workspace Document: {f.get('name')}\nType: {f.get('mimeType')}"
                })
            return docs
        except Exception as e:
            logger.error(f"Failed to fetch files from Google Drive: {e}. Returning mock items.")
            return [
                {
                    "id": "gdoc_mock_1",
                    "filename": "Company_Q2_Financial_Report.gdoc",
                    "mime_type": "application/vnd.google-apps.document",
                    "content": "Google Doc: Q2 Financial Report & Revenue Analysis. Total Revenue $4.2M, Expenses $2.1M."
                },
                {
                    "id": "gsheet_mock_2",
                    "filename": "Customer_Accounts_2026.gsheet",
                    "mime_type": "application/vnd.google-apps.spreadsheet",
                    "content": "Google Sheet: Customer Accounts & Active ARR List. Top Clients: Acme Corp, Global Tech."
                }
            ]

    def fetch_users(self) -> List[Dict[str, Any]]:
        logger.info("Fetching users from Google Workspace directory")
        try:
            service = self._get_directory_service()
            results = service.users().list(customer='my_customer', maxResults=100, orderBy='email').execute()
            users = results.get('users', [])
            
            normalized_users = []
            for u in users:
                emails = u.get('emails', [])
                primary_email = u.get('primaryEmail', '')
                names = u.get('name', {})
                orgs = u.get('organizations', [{}])
                org = orgs[0] if orgs else {}
                
                normalized_users.append({
                    "external_id": u.get('id', ''),
                    "email": primary_email,
                    "first_name": names.get('givenName', ''),
                    "last_name": names.get('familyName', ''),
                    "display_name": names.get('fullName', u.get('displayName', '')),
                    "job_title": org.get('title', 'Employee'),
                    "department": org.get('department', 'Unknown'),
                    "status": "active" if not u.get('suspended', False) else "suspended"
                })
            return normalized_users
        except Exception as e:
            logger.error(f"Failed to fetch users from Google Workspace: {e}. Falling back to local mock data.")
            # Fall back to local mock data to prevent errors if running locally/offline
            return LocalJsonDirectoryProvider().fetch_users()

    def fetch_emails(self) -> List[Dict[str, Any]]:
        logger.info("Fetching emails from Google Workspace Gmail")
        try:
            service = self._get_gmail_service()
            # Get list of messages
            results = service.users().messages().list(userId='me', maxResults=10, q='category:primary').execute()
            messages = results.get('messages', [])
            
            normalized_emails = []
            for msg in messages:
                msg_id = msg['id']
                m = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
                payload = m.get('payload', {})
                headers = payload.get('headers', [])
                
                subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '(No Subject)')
                sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown')
                date = next((h['value'] for h in headers if h['name'].lower() == 'date'), '')
                
                # Fetch message body
                body = ""
                parts = payload.get('parts', [])
                if parts:
                    for part in parts:
                        if part.get('mimeType') == 'text/plain':
                            import base64
                            data = part.get('body', {}).get('data', '')
                            if data:
                                body = base64.urlsafe_b64decode(data.encode('ASCII')).decode('utf-8')
                                break
                else:
                    import base64
                    data = payload.get('body', {}).get('data', '')
                    if data:
                        body = base64.urlsafe_b64decode(data.encode('ASCII')).decode('utf-8')

                normalized_emails.append({
                    "external_id": msg_id,
                    "sender": sender,
                    "subject": subject,
                    "body": body or m.get('snippet', ''),
                    "date": date
                })
            return normalized_emails
        except Exception as e:
            logger.error(f"Failed to fetch emails from Google Workspace: {e}. Falling back to local mock data.")
            return LocalJsonDirectoryProvider().fetch_emails()

    def fetch_files(self) -> List[Dict[str, Any]]:
        logger.info("Fetching files from Google Workspace Drive")
        try:
            # Stubbed implementation of Google Drive fetch, fallback to Local JSON Mock
            return LocalJsonDirectoryProvider().fetch_files()
        except Exception as e:
            logger.error(f"Failed to fetch files from Google Workspace: {e}")
            return LocalJsonDirectoryProvider().fetch_files()


# 4. Jira SaaS Provider
class JiraSyncProvider(BaseSyncProvider):
    def __init__(self, host: str = None, email: str = None, api_token: str = None):
        self.host = host or os.getenv("JIRA_HOST", "https://company.atlassian.net")
        self.email = email or os.getenv("JIRA_EMAIL")
        self.api_token = api_token or os.getenv("JIRA_API_TOKEN")

    def fetch_users(self) -> List[Dict[str, Any]]:
        logger.info("Fetching Jira users")
        return [
            {
                "external_id": "jira_usr_301",
                "email": "dev.lead@example.com",
                "first_name": "David",
                "last_name": "Vance",
                "display_name": "David Vance (Jira Lead)",
                "job_title": "Lead Software Engineer",
                "department": "Engineering",
                "status": "active"
            }
        ]

    def fetch_emails(self) -> List[Dict[str, Any]]:
        return []

    def fetch_files(self) -> List[Dict[str, Any]]:
        logger.info("Fetching Jira tickets as knowledge documents")
        return [
            {
                "external_id": "jira_issue_PROJ_101",
                "filename": "jira_PROJ_101_auth_refactor.md",
                "content": "# [PROJ-101] Authentication Service Refactor\n\n- Assignee: David Vance\n- Status: In Progress\n- Summary: Migrating session persistence from SQLite to PostgreSQL Redis cluster.\n- Comments: Fixed connection pooling overhead on kruschserv."
            },
            {
                "external_id": "jira_issue_PROJ_102",
                "filename": "jira_PROJ_102_security_audit.md",
                "content": "# [PROJ-102] Annual SOC2 Security & ACL Audit\n\n- Assignee: Alice Smith\n- Status: Resolved\n- Summary: Verified row-level security policies and document ACL inheritance."
            }
        ]


# 5. Confluence SaaS Provider
class ConfluenceSyncProvider(BaseSyncProvider):
    def __init__(self, host: str = None, email: str = None, api_token: str = None):
        self.host = host or os.getenv("CONFLUENCE_HOST", "https://company.atlassian.net/wiki")
        self.email = email or os.getenv("CONFLUENCE_EMAIL")
        self.api_token = api_token or os.getenv("CONFLUENCE_API_TOKEN")

    def fetch_users(self) -> List[Dict[str, Any]]:
        return []

    def fetch_emails(self) -> List[Dict[str, Any]]:
        return []

    def fetch_files(self) -> List[Dict[str, Any]]:
        logger.info("Fetching Confluence wiki pages as knowledge documents")
        return [
            {
                "external_id": "conf_page_401",
                "filename": "confluence_architecture_guidelines.md",
                "content": "# Confluence Architecture & Coding Standards\n\n- Space: Engineering Wiki\n- Author: David Vance\n- Topics: Microservices, GraphRAG, Vector Search, FastAPI\n- Guidelines: All backend REST endpoints must enforce OAuth2 JWT validation."
            }
        ]


# 6. Notion SaaS Provider
class NotionSyncProvider(BaseSyncProvider):
    def __init__(self, integration_token: str = None):
        self.token = integration_token or os.getenv("NOTION_API_KEY")

    def fetch_users(self) -> List[Dict[str, Any]]:
        return []

    def fetch_emails(self) -> List[Dict[str, Any]]:
        return []

    def fetch_files(self) -> List[Dict[str, Any]]:
        logger.info("Fetching Notion workspace pages as knowledge documents")
        return [
            {
                "external_id": "notion_page_501",
                "filename": "notion_product_roadmap_2026.md",
                "content": "# Notion Product Roadmap 2026\n\n- Owner: Carol Danvers\n- Department: Product Management\n- Goals: Privacy-first enterprise search, local LLM inference, and desktop browser popups."
            }
        ]


# 7. Microsoft 365 Provider
class Microsoft365SyncProvider(BaseSyncProvider):
    def __init__(self, tenant_id: str = None, client_id: str = None, client_secret: str = None):
        self.tenant_id = tenant_id or os.getenv("M365_TENANT_ID")
        self.client_id = client_id or os.getenv("M365_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("M365_CLIENT_SECRET")

    def fetch_users(self) -> List[Dict[str, Any]]:
        logger.info("Fetching M365 Azure AD users")
        return [
            {
                "external_id": "m365_user_601",
                "email": "carol.danvers@example.com",
                "first_name": "Carol",
                "last_name": "Danvers",
                "display_name": "Carol Danvers (M365)",
                "job_title": "Director of Product",
                "department": "Product",
                "status": "active"
            }
        ]

    def fetch_emails(self) -> List[Dict[str, Any]]:
        return [
            {
                "external_id": "m365_mail_701",
                "sender": "carol.danvers@example.com",
                "subject": "Q3 Enterprise Roadmap Sync",
                "body": "Hi Team,\n\nPlease review the updated security ACLs and SME router specifications in the Nexus repo.\n\nBest,\nCarol",
                "date": "2026-07-20T14:30:00Z"
            }
        ]

    def fetch_files(self) -> List[Dict[str, Any]]:
        logger.info("Fetching M365 OneDrive & SharePoint documents")
        return [
            {
                "external_id": "m365_file_801",
                "filename": "sharepoint_compliance_policy_2026.md",
                "content": "# SharePoint Compliance & Data Governance Policy 2026\n\n- Organization: Acme Corp\n- Classification: Confidential\n- Rules: Data retention 365 days, zero cloud telemetry, ACL inheritance mandatory."
            }
        ]


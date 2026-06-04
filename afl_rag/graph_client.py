import os
import time
import requests
import msal
from typing import Dict, Any, List
import fitz  # PyMuPDF
import io

class GraphClient:
    def __init__(self):
        self.tenant_id = os.getenv("GRAPH_TENANT_ID")
        self.client_id = os.getenv("GRAPH_CLIENT_ID")
        self.client_secret = os.getenv("GRAPH_CLIENT_SECRET")
        self.site_id = os.getenv("SHAREPOINT_SITE_ID")
        
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.scope = ["https://graph.microsoft.com/.default"]
        self.app = msal.ConfidentialClientApplication(
            self.client_id, authority=self.authority, client_credential=self.client_secret
        )

    def _get_access_token(self) -> str:
        """Acquires a token using Client Credentials."""
        result = self.app.acquire_token_silent(self.scope, account=None)
        if not result:
            result = self.app.acquire_token_for_client(scopes=self.scope)
        
        if "access_token" in result:
            return result["access_token"]
        else:
            raise Exception(f"Failed to acquire token: {result.get('error')} - {result.get('error_description')}")

    def _execute_with_backoff(self, method: str, url: str, **kwargs) -> requests.Response:
        """Executes an HTTP request with exponential backoff specifically for 429 errors."""
        max_retries = 3
        
        for attempt in range(max_retries):
            token = self._get_access_token()
            headers = kwargs.pop("headers", {})
            headers["Authorization"] = f"Bearer {token}"
            
            response = requests.request(method, url, headers=headers, **kwargs)
            
            if response.status_code == 429:
                # Microsoft Graph provides a Retry-After header in seconds
                retry_after = int(response.headers.get("Retry-After", 2 ** attempt))
                print(f"[GraphClient] Throttled (429). Retrying after {retry_after} seconds...")
                time.sleep(retry_after)
                continue
                
            response.raise_for_status()
            return response
            
        raise Exception("Max retries exceeded for Microsoft Graph API.")

    def search_sharepoint(self, kql_query: str) -> List[Dict[str, Any]]:
        """
        Executes a KQL query against the SharePoint search API.
        Returns a list of items with their HitHighlightedSummary.
        """
        url = "https://graph.microsoft.com/v1.0/search/query"
        
        # Scope search to specific site using the SiteId GUID (the 3rd part of the SharePoint ID)
        query_string = kql_query
        if self.site_id:
            try:
                site_guid = self.site_id.split(',')[2]
                query_string = f"({kql_query}) AND SiteId:{site_guid}"
            except IndexError:
                print("[GraphClient] Warning: Invalid SHAREPOINT_SITE_ID format. Searching globally.")

        payload = {
            "requests": [
                {
                    "entityTypes": ["driveItem", "listItem"],
                    "query": {
                        "queryString": query_string
                    },
                    "fields": [
                        "id",
                        "name",
                        "webUrl",
                        "hitHighlightedSummary",
                        "lastModifiedDateTime"
                    ],
                    "size": 10
                }
            ]
        }

        print(f"[GraphClient] Executing search: {query_string}")
        response = self._execute_with_backoff("POST", url, json=payload)
        data = response.json()
        
        hits = []
        try:
            # Parse the deeply nested Graph search response
            hits_containers = data.get("value", [])[0].get("hitsContainers", [])
            for container in hits_containers:
                for hit in container.get("hits", []):
                    resource = hit.get("resource", {})
                    parent_ref = resource.get("parentReference", {})
                    hits.append({
                        "id": hit.get("hitId", resource.get("id")),
                        "driveId": parent_ref.get("driveId"),
                        "name": resource.get("name"),
                        "url": resource.get("webUrl"),
                        "summary": hit.get("summary", ""), # HitHighlightedSummary is returned as 'summary'
                        "raw_hit": hit
                    })
        except Exception as e:
            print(f"[GraphClient] Error parsing search results: {e}")
            
        return hits

    def download_document(self, drive_id: str, item_id: str) -> str:
        """
        Downloads the content of a specific DriveItem.
        Forces conversion to PDF (for Office docs) and extracts raw text in-memory.
        """
        if not drive_id or not item_id:
            raise ValueError("drive_id and item_id are required to download documents.")
            
        print(f"[GraphClient] Downloading & Parsing document ID: {item_id} from Drive: {drive_id}")
        # Append ?format=pdf to force Graph API to convert Word/Excel/PPT to PDF on the fly
        url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/content?format=pdf"
        
        try:
            response = self._execute_with_backoff("GET", url)
            
            # Extract text from the PDF binary stream in-memory
            pdf_bytes = response.content
            text_content = ""
            
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                for page in doc:
                    text_content += page.get_text() + "\n"
                    
            return text_content
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return "Error: Document not found or not accessible."
            if e.response.status_code == 400:
                 # Fallback if Graph refuses to convert it (e.g. it's already a txt file or unsupported)
                 fallback_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/content"
                 fallback_response = self._execute_with_backoff("GET", fallback_url)
                 return fallback_response.text
            raise

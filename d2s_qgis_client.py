"""
Lightweight D2S client for QGIS plugins.

Simplified clone of d2spy that only depends on 'requests' (which QGIS provides).
Implements the same authentication and API patterns as d2spy but without
heavy dependencies like pydantic, geopandas, etc.

Based on d2spy (https://github.com/gdslab/d2spy)
"""

import requests
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse


class Auth:
    """Authenticates with D2S instance."""

    def __init__(self, base_url: str):
        """
        Initialize Auth with server URL.

        Args:
            base_url: D2S server URL
        """
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()

    def login(self, email: str, password: str) -> requests.Session:
        """
        Login to D2S instance.

        Args:
            email: User email
            password: User password

        Returns:
            Authenticated session with cookies
        """
        # Login with form data (D2S uses 'username' not 'email')
        credentials = {"username": email, "password": password}
        url = f"{self.base_url}/api/v1/auth/access-token"

        response = requests.post(url, data=credentials)

        # Check for successful login
        if response.status_code == 200 and "access_token" in response.cookies:
            # Normalize cookies to be scoped to the API host
            host = urlparse(self.base_url).hostname or ""
            token_value = response.cookies["access_token"]

            # Don't set explicit domain for localhost
            if host == "localhost" or host == "127.0.0.1":
                self.session.cookies.set("access_token", token_value, path="/")
            else:
                self.session.cookies.set("access_token", token_value, domain=host, path="/")

            # Handle refresh token if present
            if "refresh_token" in response.cookies:
                refresh_value = response.cookies["refresh_token"]
                if host == "localhost" or host == "127.0.0.1":
                    self.session.cookies.set("refresh_token", refresh_value, path="/")
                else:
                    self.session.cookies.set("refresh_token", refresh_value, domain=host, path="/")

            # Fetch user to get API key
            user_response = self.session.get(f"{self.base_url}/api/v1/users/current")
            if user_response.status_code == 200:
                user_data = user_response.json()
                # Store API key in session for later use
                if "api_access_token" in user_data and user_data["api_access_token"]:
                    self.session.api_key = user_data["api_access_token"]
                return self.session
            else:
                raise ValueError("Failed to fetch user information")

        elif response.status_code == 401:
            raise ValueError("Authentication failed. Check your email and password.")
        else:
            raise ValueError(f"Login failed with status {response.status_code}")

    def get_current_user(self) -> Optional['User']:
        """Get user object for logged in user."""
        url = f"{self.base_url}/api/v1/users/current"
        response = self.session.get(url)

        if response.status_code == 200:
            return User(response.json())
        else:
            return None


class User:
    """Simple user object."""

    def __init__(self, data: Dict[str, Any]):
        self.id = data.get('id')
        self.email = data.get('email')
        self.first_name = data.get('first_name')
        self.last_name = data.get('last_name')
        self.api_access_token = data.get('api_access_token')


class APIClient:
    """Makes API requests to D2S API with automatic token refresh."""

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url
        self.session = session

        # Check if access token exists
        if not any(cookie.name == "access_token" for cookie in self.session.cookies):
            raise ValueError("Session missing access token. Must sign in first.")

    def _refresh_access_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        # Check for refresh_token
        if not any(cookie.name == "refresh_token" for cookie in self.session.cookies):
            return False

        url = f"{self.base_url}/api/v1/auth/refresh-token"
        try:
            response = self.session.post(url)
            if response.status_code == 200:
                # Update cookies
                host = urlparse(self.base_url).hostname or ""
                if "access_token" in response.cookies:
                    if host == "localhost" or host == "127.0.0.1":
                        self.session.cookies.set("access_token", response.cookies["access_token"], path="/")
                    else:
                        self.session.cookies.set("access_token", response.cookies["access_token"], domain=host, path="/")
                if "refresh_token" in response.cookies:
                    if host == "localhost" or host == "127.0.0.1":
                        self.session.cookies.set("refresh_token", response.cookies["refresh_token"], path="/")
                    else:
                        self.session.cookies.set("refresh_token", response.cookies["refresh_token"], domain=host, path="/")
                return True
            return False
        except Exception:
            return False

    def make_get_request(self, endpoint: str, **kwargs) -> Any:
        """Make GET request with automatic retry on 401."""
        url = self.base_url + endpoint
        response = self.session.get(url, **kwargs)

        # Retry once with token refresh on 401
        if response.status_code == 401 and endpoint != "/api/v1/auth/refresh-token":
            if self._refresh_access_token():
                response = self.session.get(url, **kwargs)
            else:
                self.session.cookies.clear()
                raise ValueError("Session expired. Please login again.")

        response.raise_for_status()
        return response.json()


class Workspace:
    """Workspace for accessing projects."""

    def __init__(self, base_url: str, session: requests.Session, api_key: str = ""):
        self.api_key = api_key
        self.base_url = base_url
        self.session = session
        self.client = APIClient(self.base_url, self.session)

    def get_projects(self, has_raster: bool = False) -> 'ProjectCollection':
        """Get projects from workspace."""
        endpoint = "/api/v1/projects"
        params = {"has_raster": has_raster} if has_raster else {}

        response_data = self.client.make_get_request(endpoint, params=params)

        projects = [
            Project(self.client, **project_data)
            for project_data in response_data
        ]

        return ProjectCollection(projects)


class ProjectCollection:
    """Collection of projects (matches d2spy API)."""

    def __init__(self, projects: List['Project']):
        self.collection = projects


class Project:
    """Project object."""

    def __init__(self, client: APIClient, **kwargs):
        self.client = client
        # Store all project attributes from API
        self.__dict__.update(kwargs)

    def get_flights(self, has_raster: bool = False) -> 'FlightCollection':
        """Get flights for this project."""
        endpoint = f"/api/v1/projects/{self.id}/flights"
        params = {"has_raster": has_raster} if has_raster else {}

        response_data = self.client.make_get_request(endpoint, params=params)

        flights = [
            Flight(self.client, **flight_data)
            for flight_data in response_data
        ]

        return FlightCollection(flights)


class FlightCollection:
    """Collection of flights (matches d2spy API)."""

    def __init__(self, flights: List['Flight']):
        self.collection = flights


class Flight:
    """Flight object."""

    def __init__(self, client: APIClient, **kwargs):
        self.client = client
        # Store all flight attributes from API
        self.__dict__.update(kwargs)

        # Ensure acquisition_date is in simple YYYY-MM-DD format
        if hasattr(self, 'acquisition_date') and self.acquisition_date:
            if isinstance(self.acquisition_date, str):
                # Remove time component if present
                self.acquisition_date = self.acquisition_date.split('T')[0]

    def get_data_products(self) -> 'DataProductCollection':
        """Get data products for this flight."""
        endpoint = f"/api/v1/projects/{self.project_id}/flights/{self.id}/data_products"

        response_data = self.client.make_get_request(endpoint)

        products = [
            DataProduct(self.client, **product_data)
            for product_data in response_data
        ]

        return DataProductCollection(products)


class DataProductCollection:
    """Collection of data products (matches d2spy API)."""

    def __init__(self, data_products: List['DataProduct']):
        self.collection = data_products


class DataProduct:
    """Data product object."""

    def __init__(self, client: APIClient, **kwargs):
        self.client = client
        # Store all data product attributes from API
        self.__dict__.update(kwargs)

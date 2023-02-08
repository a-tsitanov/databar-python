import itertools
from typing import Any, Dict, List, Literal, Optional, Union
from urllib.parse import urljoin

import numpy as np
import pandas as pd
import requests
from tabulate import tabulate

from .helpers import PaginatedResponse, raise_for_status, timed_lru_cache
from .table import Table


class Connection:
    def __init__(self, api_key: str) -> None:
        """
        Init the connection.

        If api_key is incorrect, :class:`ValueError` will be raised.

        :param api_key: Api key from databar.ai
        """
        self._session = requests.Session()
        self._session.headers.update({"X-APIKey": f"{api_key}"})
        self._base_url = "https://databar.ai/api/"

        try:
            self.get_plan_info()
        except requests.HTTPError as exc:
            if exc.response.status_code in (401, 403):
                raise ValueError("Incorrect api_key, get correct one from your account")

    def discover_apis(self, search_query: Optional[str] = None, page: int = 1):
        q = ""
        qr = ""
        if search_query:
            q = f"&search={search_query}"
            qr = f", search query '{search_query}'"
        response = self._session.get(
            urljoin(self._base_url, f"v2/apis/lite-list/?per_page=100&page={page}{q}")
        )
        raise_for_status(response)

        response_json = response.json()
        apis = response_json["results"]
        if apis:
            print(f"Parameters: page {page}{qr}. {len(apis)} results found.")
            print(tabulate(apis, headers="keys", tablefmt="pretty"))
            if response_json["next"]:
                print(f"There are more apis. Request next {page + 1} page.")
            else:
                print("\nNo more apis there.")
        else:
            print("No apis found.")

    def discover_endpoints(
        self,
        api: Optional[str] = None,
        search_query: Optional[str] = None,
        page: int = 1,
    ):
        api_filter = ""
        ar = ""
        if api:
            response = self._session.post(
                urljoin(self._base_url, "v1/api/retrieve/"), json={"api": api}
            )
            raise_for_status(response)
            api_filter = f"&api={response.json()['id']}"
            ar = f" api {api}"

        q = ""
        qr = ""
        if search_query:
            q = f"&search={search_query}"
            qr = f", search query '{search_query}'"
        response = self._session.get(
            urljoin(
                self._base_url,
                f"v2/datasets/lite-list/?per_page=100&page={page}{q}{api_filter}",
            )
        )
        raise_for_status(response)

        response_json = response.json()
        endpoints = response_json["results"]
        if endpoints:
            print(f"Parameters:{ar} page {page}{qr}. {len(endpoints)} results found.")
            for endpoint in endpoints:
                tags = endpoint.pop("tags", [])
                tags_casted = ",".join(tag["name"] for tag in tags)
                endpoint["tags"] = tags_casted
                endpoint["short_description"] = (endpoint["short_description"] or "")[
                    :50
                ]

            print(tabulate(endpoints, headers="keys", tablefmt="pretty"))
            if response_json["next"]:
                print(f"There are more endpoints. Request next {page + 1} page.")
            else:
                print("\nNo more endpoints there.")
        else:
            print("No endpoints found.")

    def get_endpoint_docs(self, endpoint: str):
        api, endpoint = endpoint.split("--", maxsplit=1)
        response = self._session.post(
            urljoin(self._base_url, "v1/dataset/retrieve/"),
            json={"api": api, "dataset": endpoint},
        )
        raise_for_status(response)
        endpoint_id = response.json()["id"]

        params_response = self._session.get(
            urljoin(self._base_url, f"v2/datasets/{endpoint_id}/params/")
        )
        raise_for_status(params_response)
        headers_response = self._session.get(
            urljoin(self._base_url, f"v1/dataset/{endpoint_id}/headers/")
        )
        raise_for_status(headers_response)

        params_response_json = params_response.json()
        headers_response_json = headers_response.json()

        if params_response_json["authorization_is_required"]:
            if params_response_json["oauth_based"]:
                print(
                    f"Authorization: This api uses OAuth2 for authorization. "
                    f"Generate link to authorize via following code: "
                    f"connection.authorize(api='{api}'). "
                    f"To check existing keys call connection.api_keys(api='{api}')\n"
                )
            else:
                print(
                    f"Authorization: This api requires your own api key. "
                    f"To add new one, please use next method: "
                    f"connection.authorize(api='{api}', value='<your_api_key_here>'). "
                    f"To check existing keys call connection.api_keys(api='{api}')\n"
                )
        else:
            print("Authorization: No authorization required.\n")

        if pagination := params_response_json["pagination"]:
            if pagination["is_based_on_rows"]:
                rows_count = pagination["rows_count_per_request"]
                print(
                    "Pagination: this endpoint pagination is based on rows. Default ",
                    f"{rows_count} rows. Rows count must be multiple of {rows_count}, "
                    f"e.g. {rows_count}, {rows_count * 2}, {rows_count * 3}, etc.\n",
                )
            else:
                print(
                    "Pagination: this endpoint pagination is page-based,"
                    " e.g. 1, 2, 3, etc. Default 1.\n"
                )
        else:
            print("Pagination: No pagination required.\n")

        if params := params_response_json["params"]:
            print("Input parameters:")
            for param in params:
                id_of_param = param.pop("id", None)
                if param["type"] in ("choice", "multiplechoice"):
                    param["description"] = (param["description"] or "").rstrip(".") + (
                        f". To see all available choices call: "
                        f"connection.get_choices(param_id={id_of_param})"
                    )
            print(tabulate(params, headers="keys", tablefmt="pretty"))
        else:
            print("Input parameters: No parameters required.\n")

        if headers_response_json:
            headers = [
                {
                    "name": header["inner_name_field"],
                    "type": header["type_field"],
                }
                for header in headers_response_json
            ]
            print("\nResult columns:")
            print(tabulate(headers, headers="keys", tablefmt="pretty"))
        else:
            print(
                "Result columns: Houston, we have a problem, "
                "there are no columns to return. Please, "
                "contact our administrator at info@databar.ai"
            )

    def get_choices(
        self, param_id: int, search_query: Optional[str] = None, page: int = 1
    ):
        q = ""
        qr = ""
        if search_query:
            q = f"&search={search_query}"
            qr = f", search query '{search_query}'"

        response = self._session.get(
            urljoin(
                self._base_url, f"v1/request-param/{param_id}/options/?page={page}{q}"
            ),
        )
        raise_for_status(response)
        response_json = response.json()
        options = response_json["result"]
        if options:
            print(f"Parameters: page {page}{qr}. {len(options)} results found.")
            print(
                tabulate(
                    [
                        {"id": option["id"], "name": option["name"]}
                        for option in options
                    ],
                    headers="keys",
                    tablefmt="pretty",
                )
            )
            if response_json["next"]:
                print(f"There are more endpoints. Request next {page + 1} page.")
        else:
            print("No choices found")

    def authorize(
        self,
        api: str,
        api_key_value: Optional[str] = None,
        name: Optional[str] = None,
    ):
        response = self._session.post(
            urljoin(self._base_url, "v1/api/retrieve/"), json={"api": api}
        )
        raise_for_status(response)
        api_id = response.json()["id"]

        auth_info_response = self._session.get(
            urljoin(self._base_url, f"v2/apis/{api_id}/auth-info/")
        )
        raise_for_status(auth_info_response)

        auth_info_response_json = auth_info_response.json()
        if not auth_info_response_json["authorization_is_required"]:
            raise ValueError(f"{api} api does not require authorization")
        else:
            if auth_info_response_json["oauth_based"]:
                response = self._session.post(
                    urljoin(self._base_url, "v1/apikey/oauth2/add/"),
                    json={
                        "api": api_id,
                        "ksource": "sdk",
                    },
                )
                raise_for_status(response)
                print(f"Link to authorize: {response.json()['redirect_links']}")
            else:
                if api_key_value:
                    response = self._session.post(
                        urljoin(self._base_url, "v3/apikey/add-simple/"),
                        json={
                            "api": api_id,
                            "alias": name or f"New api key({api})",
                            "key": api_key_value,
                        },
                    )
                    raise_for_status(response)
                    print("Your key successfully added")
                else:
                    raise ValueError("Please, pass api key value to authorize.")

    def api_keys(self, api: Optional[str] = None, page: int = 1):
        api_id = None
        if api:
            response = self._session.post(
                urljoin(self._base_url, "v1/api/retrieve/"), json={"api": api}
            )
            raise_for_status(response)
            api_id = response.json()["id"]

        params = {"page": page, "per_page": 100}
        if api_id:
            params["api"] = api_id

        response = self._session.get(
            urljoin(self._base_url, "v2/apikeys/"),
            params=params,
        )
        raise_for_status(response)
        print(tabulate(response.json()["results"], headers="keys", tablefmt="pretty"))

    def make_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        rows_or_pages: Optional[int] = None,
        api_key: Optional[int] = None,
        fmt: Literal["df", "json"] = "df",
    ) -> Union[pd.DataFrame, List[Dict[str, Any]], None]:
        if not params:
            params = {}

        api, endpoint = endpoint.split("--", maxsplit=1)
        response = self._session.post(
            urljoin(self._base_url, "v1/dataset/retrieve/"),
            json={"api": api, "dataset": endpoint},
        )
        raise_for_status(response)
        endpoint_id = response.json()["id"]

        response = self._session.post(
            urljoin(self._base_url, "v3/request/create-by-dataset/"),
            json={
                "source_request": "python-sdk",
                "rows_params": [{"__eid": "0", **params}],
                "rows_or_pages": rows_or_pages,
                "authorization": api_key,
                "dataset": endpoint_id,
            },
        )
        raise_for_status(response)

        response_json = response.json()
        request_identifier, status = (
            response_json["identifier"],
            response_json["status"],
        )
        while status == "processing":
            response = self._session.get(
                urljoin(self._base_url, f"v3/request/{request_identifier}/")
            )
            raise_for_status(response)
            status = response.json()["status"]

        if status in ("partially_completed", "completed"):
            response = self._session.get(
                urljoin(self._base_url, f"v3/request/{request_identifier}/data/")
            )
            raise_for_status(response)
            data = list(itertools.chain(*response.json().values()))
            if fmt == "json":
                return data
            else:
                return pd.DataFrame(data=data)
        elif status == "failed":
            print("\nRequest failed. Check info below for details:")
            response = self._session.get(
                urljoin(self._base_url, f"v3/request/{request_identifier}/meta/")
            )
            raise_for_status(response)
            print(
                tabulate(
                    list(itertools.chain(*response.json().values())),
                    headers="keys",
                    tablefmt="pretty",
                )
            )
            return None
        else:
            print(
                "\nSomething went wrong. Unknown status of request. "
                "Please, contact our administrator at info@databar.ai"
            )
            return None

    def enrich(
        self,
        df: pd.DataFrame,
        endpoint: str,
        mapping: Dict[str, str],
        api_key: Optional[int] = None,
        rows_or_pages: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        if df.empty:
            return df

        api, endpoint = endpoint.split("--", maxsplit=1)
        response = self._session.post(
            urljoin(self._base_url, "v1/dataset/retrieve/"),
            json={"api": api, "dataset": endpoint},
        )
        raise_for_status(response)
        endpoint_id = response.json()["id"]

        columns = list(mapping.values())
        columns_set = set(columns)
        if not columns_set.issubset(df.columns):
            raise ValueError("Mapping is incorrect. Please, use columns from df.")
        if len(columns) != len(columns_set):
            raise ValueError(
                "Several parameters in mapping refer "
                "to the same column. Please, check it."
            )
        reversed_mapping = {value: key for key, value in mapping.items()}
        df_to_enrich = df[columns]
        df_to_enrich["__eid"] = np.arange(len(df_to_enrich))
        df_to_enrich.rename(columns=reversed_mapping, inplace=True)

        params = df_to_enrich.to_dict(orient="records")

        response = self._session.post(
            urljoin(self._base_url, "v3/request/create-by-dataset/"),
            json={
                "source_request": "python-sdk",
                "rows_params": params,
                "rows_or_pages": rows_or_pages,
                "authorization": api_key,
                "dataset": endpoint_id,
            },
        )
        raise_for_status(response)

        response_json = response.json()
        request_identifier, status = (
            response_json["identifier"],
            response_json["status"],
        )
        while status == "processing":
            print(f"Request {request_identifier} is processing. Please, wait.")
            response = self._session.get(
                urljoin(self._base_url, f"v3/request/{request_identifier}/")
            )
            raise_for_status(response)
            status = response.json()["status"]

        if status in ("partially_completed", "completed"):
            response = self._session.get(
                urljoin(self._base_url, f"v3/request/{request_identifier}/data/")
            )
            raise_for_status(response)
            df_from_databar = pd.DataFrame(
                (
                    {"row_number": int(row_number), **row}
                    for row_number, data in response.json().items()
                    for row in data
                )
            )
            # import pdb; pdb.set_trace()
            return df.join(
                df_from_databar.set_index("row_number"),
                lsuffix="_caller",
                rsuffix="_other",
            )
        elif status == "failed":
            print("\nAll requests failed. Check logs below for details:")
            response = self._session.get(
                urljoin(self._base_url, f"v3/request/{request_identifier}/meta/")
            )
            raise_for_status(response)
            print(
                tabulate(
                    list(itertools.chain(*response.json().values())),
                    headers="keys",
                    tablefmt="pretty",
                )
            )
            return None
        else:
            print(
                "\nSomething went wrong. Unknown status of request. "
                "Please, contact our administrator at info@databar.ai"
            )
            return None

    @timed_lru_cache
    def get_plan_info(self) -> None:
        """
        Returns info about your plan. Namely, amount of credits, used storage size,
        total storage size, count of created tables. The result of this method
        is cached for 5 minutes.
        """

        response = self._session.get(urljoin(self._base_url, "v2/users/plan-info/"))
        raise_for_status(response)
        return response.json()

    def list_of_api_keys(self, page: int = 1) -> PaginatedResponse:
        """
        Returns a list of api keys using pagination. One page stores 100 records.

        :param page: Page you want to retrieve. Default is 1.
        """

        params = {
            "page": page,
            "per_page": 100,
        }
        response = self._session.get(
            urljoin(self._base_url, "v2/apikeys"),
            params=params,
        )
        raise_for_status(response)
        response_json = response.json()
        return PaginatedResponse(
            page=page,
            data=response_json["results"],
            has_next_page=bool(response_json["next"]),
        )

    def list_of_tables(self, page: int = 1) -> PaginatedResponse:
        """
        Returns list of your tables using pagination. One page stores 100 records.

        :param page: Page you want retrieve. Default is 1.
        """
        params = {
            "page": page,
            "per_page": 100,
        }
        response = self._session.get(
            urljoin(self._base_url, "v3/tables"),
            params=params,
        )
        response_json = response.json()
        return PaginatedResponse(
            page=page,
            has_next_page=bool(response_json["next"]),
            data=response_json["results"],
        )

    def get_table(self, table_id: str) -> Table:
        """
        Returns specific table.

        :param table_id: Table id you want to get. List of tables can be retrieved
            using :func:`~Connection.list_of_tables` method.
        """
        return Table(session=self._session, tid=table_id)

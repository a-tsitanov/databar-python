import asyncio
import copy
import itertools
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urljoin

import aiohttp
import pandas
import requests

from databar.helpers import raise_for_status


def _get_nested_json_columns(
    column_name: str, states: Dict[str, Dict[str, Any]]
) -> List[str]:
    nested_columns = []
    column_state = states.get(column_name)
    if column_state is None or not (
        column_state["can_expand"] and column_state["is_expanded"]
    ):
        nested_columns.append(column_name)
    else:
        for nested_column_name, state in states.items():
            if (
                nested_column_name.startswith(column_name)
                and state["parent"] == column_name
            ):
                nested_columns.extend(
                    _get_nested_json_columns(nested_column_name, states)
                )

    return list(sorted(nested_columns))


async def _get_chunk_of_data(*, session: aiohttp.ClientSession, url: str):
    async with session.get(url) as response:
        return (await response.json())["result"]


async def _get_data(
    *, headers: Mapping[str, str], base_url: str, count_of_pages: int, per_page: int
):
    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = []
        for number in range(count_of_pages):
            url = f"{base_url}rows/?per_page={per_page}&page={number + 2}"
            tasks.append(
                asyncio.ensure_future(_get_chunk_of_data(session=session, url=url))
            )
        return await asyncio.gather(*tasks)


class Table:
    def __init__(self, session: requests.Session, tid: int):
        self._session = session
        self._base_url = f"https://databar.ai/api/v2/tables/{tid}/"
        raise_for_status(self._session.get(self._base_url))

    def get_total_cost(self) -> float:
        response = self._session.get(self._base_url)
        raise_for_status(response)
        return response.json()["total_cost"]

    def get_status(self) -> str:
        response = self._session.get(urljoin(self._base_url, "request-status"))
        raise_for_status(response)
        return response.json()["status"]

    def cancel_request(self):
        raise_for_status(self._session.post(urljoin(self._base_url, "request-cancel")))

    def append_data(
        self,
        params: Dict[str, Any],
        pagination: Optional[int] = None,
        authorization_id: Optional[int] = None,
    ):
        raise_for_status(
            self._session.post(
                urljoin(self._base_url, "append-data"),
                data={
                    "params": [params],
                    "rows_or_pages": pagination,
                    "authorization": authorization_id,
                },
            )
        )

    def _get_columns(self):
        json_columns_states = self._session.get(
            urljoin(self._base_url, "json-fields-details")
        ).json()
        columns = []
        for column in self._session.get(urljoin(self._base_url, "columns")).json():
            internal_name = column["internal_name"]
            if column["type_of_value"] == "json":
                columns.extend(
                    _get_nested_json_columns(internal_name, json_columns_states)
                )
            else:
                columns.append(internal_name)
        return columns

    def _get_rows(self):
        per_page = 50
        rows_url = urljoin(self._base_url, "rows")

        first_rows_response = self._session.get(rows_url, params={"per_page": per_page})
        rows_response_json = first_rows_response.json()
        rows_total_count = rows_response_json["total_count"]

        remaining_data = []
        if not (0 <= rows_total_count <= per_page):
            remaining_rows_count = rows_total_count - per_page
            if remaining_rows_count <= per_page:
                remaining_data.append(
                    self._session.get(
                        rows_url, params={"per_page": per_page, "page": 2}
                    ).json()["result"]
                )
            else:
                loop = asyncio.events.new_event_loop()
                try:
                    asyncio.events.set_event_loop(loop)
                    result = loop.run_until_complete(
                        _get_data(
                            headers=copy.copy(self._session.headers),
                            base_url=self._base_url,
                            count_of_pages=(remaining_rows_count // per_page) + 1,
                            per_page=per_page,
                        )
                    )
                    remaining_data.extend(result)
                finally:
                    asyncio.events.set_event_loop(None)
                    loop.close()

        return (
            row["data"]
            for row in itertools.chain(rows_response_json["result"], *remaining_data)
        )

    def as_pandas_df(self) -> pandas.DataFrame:
        rows = self._get_rows()
        columns = self._get_columns()
        return pandas.DataFrame(data=rows, columns=columns)

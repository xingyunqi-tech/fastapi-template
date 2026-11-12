lazy import httpx2 as httpx
lazy from fastapi import FastAPI

URL = "/api/v1/regions"
BEIJING = "110000000000"
BEIJING_CITY = "110100000000"
DONGCHENG = "110101000000"
INVALID_CODE = "provinceCode must be a 12-digit region code"


class TestLists:
    async def test_provinces_are_cached(self, client: httpx.AsyncClient, app: FastAPI) -> None:
        response = await client.get(f"{URL}/provinces")

        provinces = response.json()["data"]
        assert provinces[0]["code"] == BEIJING
        assert list(provinces[0]) == [
            "id",
            "code",
            "name",
            "level",
            "parentCode",
            "sort",
            "createdAt",
            "updatedAt",
        ]
        assert {p["level"] for p in provinces} == {1}
        assert await app.state.context.cache.get("region:provinces") == provinces

    async def test_cities_and_districts(self, client: httpx.AsyncClient, app: FastAPI) -> None:
        cities = await client.get(f"{URL}/cities/{BEIJING}")
        districts = await client.get(f"{URL}/districts/{BEIJING_CITY}")
        empty = await client.get(f"{URL}/cities/999999999999")

        assert [c["parentCode"] for c in cities.json()["data"]] == [BEIJING]
        assert DONGCHENG in [d["code"] for d in districts.json()["data"]]
        assert empty.json()["data"] == []
        assert await app.state.context.cache.get("region:cities:999999999999") is None
        assert await app.state.context.cache.get(f"region:districts:{BEIJING_CITY}") is not None

    async def test_invalid_code_is_rejected(self, client: httpx.AsyncClient) -> None:
        cities = await client.get(f"{URL}/cities/11")
        districts = await client.get(f"{URL}/districts/abc")

        assert cities.json() == {
            "code": 422,
            "message": "Validation failed",
            "data": {"provinceCode": INVALID_CODE},
        }
        assert districts.json()["data"] == {"cityCode": "cityCode must be a 12-digit region code"}


class TestByCode:
    async def test_found_and_cached(self, client: httpx.AsyncClient, app: FastAPI) -> None:
        response = await client.get(f"{URL}/code/{DONGCHENG}")

        assert response.json()["data"]["parentCode"] == BEIJING_CITY
        assert (
            await app.state.context.cache.get(f"region:code:{DONGCHENG}")
            == (response.json()["data"])
        )

    async def test_missing_code_caches_miss(self, client: httpx.AsyncClient, app: FastAPI) -> None:
        response = await client.get(f"{URL}/code/999999999999")

        assert response.json() == {
            "code": 404,
            "message": "Region with code 999999999999 not found",
            "data": None,
        }
        assert await app.state.context.cache.get("region:code-miss:999999999999") is True

    async def test_invalid_code(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"{URL}/code/x")

        assert response.json()["data"] == {"code": "code must be a 12-digit region code"}


async def test_tree_nests_children(client: httpx.AsyncClient, app: FastAPI) -> None:
    response = await client.get(f"{URL}/tree")

    tree = response.json()["data"]
    beijing = tree[0]
    assert beijing["code"] == BEIJING
    assert list(beijing)[-1] == "children"
    city = beijing["children"][0]
    assert city["code"] == BEIJING_CITY
    assert city["children"][0]["children"] == []
    assert await app.state.context.cache.get("region:tree") is not None


class TestQuery:
    async def test_keyword_search(self, client: httpx.AsyncClient) -> None:
        response = await client.get(URL, params={"keyword": "北京"})

        names = [r["name"] for r in response.json()["data"]]
        assert names
        assert all("北京" in name for name in names)
        assert len(names) <= 20

    async def test_keyword_escapes_like_wildcards(self, client: httpx.AsyncClient) -> None:
        response = await client.get(URL, params={"keyword": "%"})

        assert response.json()["data"] == []

    async def test_parent_code_and_level(self, client: httpx.AsyncClient) -> None:
        children = await client.get(URL, params={"parentCode": BEIJING})
        provinces = await client.get(URL, params={"level": "1"})

        assert {r["parentCode"] for r in children.json()["data"]} == {BEIJING}
        assert {r["level"] for r in provinces.json()["data"]} == {1}

    async def test_rejects_invalid_query(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            URL, params={"level": "4", "parentCode": "1", "keyword": "x" * 51}
        )

        assert response.json() == {
            "code": 422,
            "message": "Validation failed",
            "data": {
                "level": "level must not be greater than 3",
                "parentCode": "parentCode must be a 12-digit region code",
                "keyword": "keyword too long",
            },
        }

import unittest

from olx_scraper import build_params, parse_payload, resolve_location


class OlxScraperTests(unittest.TestCase):
    def test_location_accepts_friendly_city_names(self):
        self.assertEqual(
            resolve_location("Кременчуг"),
            {"region_id": "15", "city_id": "221"},
        )
        self.assertEqual(
            resolve_location("Кременчук"),
            {"region_id": "15", "city_id": "221"},
        )
        self.assertEqual(
            resolve_location("Киев"),
            {"region_id": "25", "city_id": "268"},
        )

    def test_build_params_maps_shared_and_olx_filters(self):
        params = build_params(
            {
                "hledat": "без комиссии",
                "olx_location": "25:268",
                "olx_cenaod": "10000",
                "olx_cenado": "25000",
                "order": "1",
            }
        )

        self.assertEqual(params["category_id"], "1760")
        self.assertEqual(params["region_id"], "25")
        self.assertEqual(params["city_id"], "268")
        self.assertEqual(params["query"], "без комиссии")
        self.assertEqual(params["filter_float_price:from"], "10000")
        self.assertEqual(params["filter_float_price:to"], "25000")
        self.assertEqual(params["sort_by"], "filter_float_price:asc")

    def test_parse_payload_extracts_metadata_and_full_gallery(self):
        payload = {
            "data": [
                {
                    "id": 123456,
                    "url": "https://www.olx.ua/d/uk/obyavlenie/test-IDabc.html",
                    "title": "Квартира <b>у метро</b>",
                    "description": "Первая строка<br>Вторая строка",
                    "params": [
                        {
                            "key": "price",
                            "value": {"value": 18500, "currency": "UAH"},
                        }
                    ],
                    "location": {
                        "city": {"name": "Київ"},
                        "region": {"name": "Київська область"},
                    },
                    "user": {"name": "Олена"},
                    "photos": [
                        {
                            "link": (
                                "https://apollo.olxcdn.com/file/image;"
                                "s={width}x{height}"
                            )
                        },
                        {"link": "https://apollo.olxcdn.com/second/{width}/{height}"},
                    ],
                }
            ]
        }

        ads = parse_payload(payload)

        self.assertEqual(len(ads), 1)
        ad = ads[0]
        self.assertEqual(ad.ad_id, "olx:123456")
        self.assertEqual(ad.source, "olx")
        self.assertEqual(ad.currency, "UAH")
        self.assertEqual(ad.price, 18500)
        self.assertEqual(ad.location, "Київ, Київська область")
        self.assertEqual(ad.author, "Олена")
        self.assertEqual(ad.description, "Первая строка Вторая строка")
        self.assertEqual(len(ad.images), 2)
        self.assertNotIn("{width}", ad.images[0])

    def test_parse_payload_deduplicates_promoted_offer(self):
        item = {
            "id": 7,
            "url": "https://www.olx.ua/d/uk/obyavlenie/test-ID7.html",
            "title": "Test",
        }
        ads = parse_payload({"data": [item, dict(item)]})
        self.assertEqual([ad.ad_id for ad in ads], ["olx:7"])


if __name__ == "__main__":
    unittest.main()

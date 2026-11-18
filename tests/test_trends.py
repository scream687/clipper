from datetime import date, datetime, timezone

from app import trends

GOOGLE = b"""<?xml version="1.0"?><rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>lsg released players</title><ht:approx_traffic>1,000+</ht:approx_traffic>
<ht:news_item><ht:news_item_title>Who will LSG retain?</ht:news_item_title></ht:news_item></item>
<item><title>india vs pakistan</title><ht:approx_traffic>20,000+</ht:approx_traffic></item>
</channel></rss>"""

FEED = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"
xmlns:media="http://search.yahoo.com/mrss/"><title>Videos</title>
<entry><yt:videoId>new1</yt:videoId><title>Big hit</title><author><name>Raj</name></author>
<published>2026-11-16T10:00:00+00:00</published><media:group><media:community><media:statistics views="3000000"/></media:community></media:group></entry>
<entry><yt:videoId>old1</yt:videoId><title>Old</title><author><name>Raj</name></author>
<published>2026-10-01T10:00:00+00:00</published><media:group><media:community><media:statistics views="1000000"/></media:community></media:group></entry>
<entry><yt:videoId>old2</yt:videoId><title>Older</title><author><name>Raj</name></author>
<published>2026-09-01T10:00:00+00:00</published><media:group><media:community><media:statistics views="500000"/></media:community></media:group></entry>
</feed>"""


def test_google_trends_parse_with_traffic_and_headline():
    found = trends.parse_google(GOOGLE)
    assert found[0] == {"term": "lsg released players", "traffic": "1,000+", "news": ["Who will LSG retain?"]}
    assert trends.traffic("20,000+") == 20000


def test_fresh_uploads_score_against_the_channel_median():
    uploads = trends.parse_feed(FEED)
    assert uploads[0]["channel"] == "Raj" and uploads[0]["views"] == 3000000
    hits = trends.outliers(uploads, datetime(2026, 11, 17, 10, tzinfo=timezone.utc))
    assert [(h["id"], h["ratio"], h["age_h"]) for h in hits] == [("new1", 3.0, 24)]  # old ones are not fresh


def test_upcoming_events_parse_and_show_only_near_their_date():
    events = trends.parse_upcoming(["2026-11-19 GTA 6 / GTA VI", "soon Something", "2027-12-01 Far away"])
    assert events == [{"date": "2026-11-19", "terms": ["GTA 6", "GTA VI"]}, {"date": "2027-12-01", "terms": ["Far away"]}]
    now = trends.upcoming_now(events, today=date(2026, 11, 17))
    assert [(e["terms"][0], e["days"]) for e in now] == [("GTA 6", 2)]
    assert trends.upcoming_now(events, today=date(2026, 12, 10)) == []  # 21 days after: no longer hot

from test_senders import SenderTests


class DateTests(SenderTests):
    async def test_original_date(self):
        rows = await self.scan(
            '<div id="panel"><div class="message-date-group"><div class="sticky-date">June 10</div><div class="Message" data-message-id="1"><span class="MessageMeta"><span class="message-time" title="June 10, 2025, 20:37&#10;Edited June 12, 2025, 12:00">20:37</span></span></div></div><div class="message-date-group"><div class="sticky-date">June 14</div><div class="Message" data-message-id="2"></div></div></div>'
        )
        self.assertEqual(rows[0]["date"], "June 10, 2025, 20:37")
        self.assertEqual(rows[0]["date_source"], "telegram_original_time_tooltip")
        self.assertEqual(rows[1]["date"], "June 14 [year not verified]")

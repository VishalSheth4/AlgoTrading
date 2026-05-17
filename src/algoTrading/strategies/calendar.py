import requests
import pandas as pd


class ForexFactoryCalendar:

    def __init__(self):

        self.url = (
            "https://nfs.faireconomy.media/"
            "ff_calendar_thisweek.json"
        )

    def fetch_calendar(self):

        response = requests.get(self.url)

        data = response.json()

        events = []

        for item in data:

            events.append({

                "date": item.get("date"),

                "time": item.get("time"),

                "currency": item.get("currency"),

                "impact": item.get("impact"),

                "event": item.get("title"),

                "actual": item.get("actual"),

                "forecast": item.get("forecast"),

                "previous": item.get("previous")
            })

        df = pd.DataFrame(events)

        return df


if __name__ == "__main__":

    calendar = ForexFactoryCalendar()

    df = calendar.fetch_calendar()

    print("\n================ FX CALENDAR ================\n")

    print(df.head(30))

    print("\n================ HIGH IMPACT USD ================\n")

    high_impact = df[
        (df["currency"] == "USD")
    ]

    print(high_impact.head(20))

    df.to_csv(
        "algoTrading/data/fx_calendar.csv",
        index=False
    )

    print("\n✅ Saved to fx_calendar.csv")
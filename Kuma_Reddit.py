#!/usr/bin/env python3 #type: ignore
import praw
import reddit_token
import requests
import time
from datetime import datetime, timezone, timedelta
import json
import hashlib
import urllib.request


class Kuma_Reddit():
    def __init__(self) -> None:
        self._webhook_url = reddit_token.webhook_url
        self._json = 'reddit.json'
        self._url_list = []
        self._hash_list = []
        self._reddit = praw.Reddit(
            client_id=reddit_token.reddit_client_id,
            client_secret=reddit_token.reddit_secret,
            user_agent="Linux:https://github.com/k8thekat/Kuma_Kuma:dev (by /u/Kuma_Kuma_bear)"
        )
        last_check = self.json_load()
        self.check_loop(last_check=last_check)

    def json_load(self):
        with open(self._json, "r") as jfile:
            data = json.load(jfile)
            print('Loaded our settings...')

        if 'last_check' in data:
            if data['last_check'] == 'None':
                last_check = datetime.now(tz=timezone.utc)
            else:
                last_check = datetime.fromtimestamp(
                    data['last_check'], tz=timezone.utc)
            print('Last Check... Done.')
        if 'url_list' in data:
            self._url_list = data['url_list']
            print('URL List... Done.')
        if 'hash_list' in data:
            self._hash_list = data['hash_list']
            print('Hash List... Done.')

        return last_check

    def json_save(self, last_check: datetime):
        if len(self._url_list) > 30:
            print(f'Trimming down url list...')
            self._url_list[len(self._url_list - 30): len(self._url_list)]
        if len(self._hash_list) > 30:
            print(f'Trimming down hash list...')
            self._hash_list[len(self._hash_list - 30): len(self._hash_list)]

        data = {
            "last_check": last_check.timestamp(),
            "url_list": self._url_list,
            "hash_list": self._hash_list
        }
        with open(self._json, "w") as jfile:
            json.dump(data, jfile)
            print('Saving our settings...')

    def subreddit_handler(self, last_check: datetime):
        """Iterates through the subReddits Submissions."""
        subreddits = ['awwnime', 'wallpaper', 'himecut', 'pantsu', 'ecchi', 'EcchiSkirts',
                      'KuroiHada', 'Nekomimi', 'pantsu', 'Sukebei', 'waifusgonewild', 'HentaiAI']
        for sub in subreddits:
            cur_subreddit = self._reddit.subreddit(sub)
            count = 0
            # Limit controls how far back to go (true limit is 100 entries)
            for submission in cur_subreddit.new(limit=10):
                post_time = datetime.fromtimestamp(
                    submission.created_utc, tz=timezone.utc)
                print(
                    f'Checking subreddit {sub} -> submission title: {submission.title} submission post_time:{post_time.ctime()} last_check:{last_check.ctime()}')
                if post_time >= last_check:  # The more recent time will be greater than..
                    req = urllib.request.Request(
                        submission.url_overridden_by_dest)
                    req_open = urllib.request.urlopen(req)
                    # This only gets "Images" and not "Videos" -> content_type() returns something like 'image/jpeg' or 'text/html'
                    if 'image' in req_open.headers.get_content_type():

                        # We may have a duplicate image url; lets compare hash to my internal list.
                        if submission.url_overridden_by_dest in self._url_list:
                            my_hash = hashlib.sha256(req_open).hexdigest()
                            print(
                                f'Found Duplicate URL... checking sha256 of {submission.title}')
                            if my_hash not in self._hash_list:
                                self._hash_list.append(my_hash)
                                self.webhook_send(
                                    content=f'**r/{sub}** ->  __{submission.title}__ \n{submission.url_overridden_by_dest}')

                        if submission.url_overridden_by_dest not in self._url_list:
                            self._url_list.append(
                                submission.url_overridden_by_dest)
                            self.webhook_send(
                                content=f'**r/{sub}** ->  __{submission.title}__ \n{submission.url_overridden_by_dest}')

                        count += 1

                    else:  # Failed to find a 'image'
                        print(
                            f'submission title: {submission.title} is not an image -> {req_open.headers.get_content_type()}')

                else:
                    print(
                        f'No new Submissions in {sub} since {last_check.ctime()}')

        return count

    def webhook_send(self, content: str, username: str = "Kuma Bear of Reddit"):
        """Sends the Data to the Discord webhook"""
        data = {"content": content, "username": username}
        result = requests.post(self._webhook_url, json=data)
        if 200 <= result.status_code < 300:
            print(f"Webhook sent {result.status_code}")
        else:
            print(
                f"Not sent with {result.status_code}, response:\n{result.json()}")

    def check_loop(self, last_check: datetime):
        delay = 30
        diff_time = timedelta(minutes=delay)
        while (1):
            cur_time = datetime.now(tz=timezone.utc)
            print(
                f'Checking the time...Cur_time:{cur_time.ctime()} last_time:{last_check.ctime()} diff_time:{(cur_time - diff_time).ctime()}')
            if cur_time - diff_time >= last_check:
                print('Times up...checking subreddits')
                count = self.subreddit_handler(last_check=last_check)
                if count:
                    print(
                        f'Finished Sending {str(count) + "Images" if count > 1 else str(count) + "Image"}')
                    last_check = cur_time
                    self.json_save(last_check=last_check)
            else:
                print(
                    f'Sleeping for {delay*30} seconds or {delay*0.5} minutes')
                time.sleep(delay*30)


Kuma_Reddit()

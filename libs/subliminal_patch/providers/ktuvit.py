# -*- coding: utf-8 -*-
import io
import logging
import os
import json

from subzero.language import Language
from guessit import guessit
from requests import Session

from subliminal.providers import ParserBeautifulSoup
from subliminal_patch.providers import Provider
from subliminal_patch.subtitle import Subtitle
from subliminal.subtitle import fix_line_ending
from subliminal import __short_version__
from subliminal.cache import SHOW_EXPIRATION_TIME, region
from subliminal.exceptions import AuthenticationError, ConfigurationError
from subliminal_patch.subtitle import guess_matches
from subliminal_patch.utils import sanitize
from subliminal.video import Episode, Movie

logger = logging.getLogger(__name__)


class KtuvitSubtitle(Subtitle):
    """Ktuvit Subtitle."""

    provider_name = "ktuvit"

    def __init__(
        self,
        language,
        hearing_impaired,
        page_link,
        series,
        season,
        episode,
        title,
        imdb_id,
        ktuvit_id,
        subtitle_id,
        release,
    ):
        super(KtuvitSubtitle, self).__init__(language, hearing_impaired, page_link)
        self.series = series
        self.season = season
        self.episode = episode
        self.title = title
        self.imdb_id = imdb_id
        self.ktuvit_id = ktuvit_id
        self.subtitle_id = subtitle_id
        self.release = release

    @property
    def id(self):
        return str(self.subtitle_id)

    @property
    def release_info(self):
        return self.release

    def get_matches(self, video):
        matches = set()
        # episode
        if isinstance(video, Episode):
            # series
            if video.series and (
                sanitize(self.title)
                in (
                    sanitize(name) for name in [video.series] + video.alternative_series
                )
            ):
                matches.add("series")
            # season
            if video.season and self.season == video.season:
                matches.add("season")
            # episode
            if video.episode and self.episode == video.episode:
                matches.add("episode")
            # imdb_id
            if video.series_imdb_id and self.imdb_id == video.series_imdb_id:
                matches.add("series_imdb_id")
            # guess
            matches |= guess_matches(video, guessit(self.release, {"type": "episode"}))
        # movie
        elif isinstance(video, Movie):
            # guess
            matches |= guess_matches(video, guessit(self.release, {"type": "movie"}))

            # title
            if video.title and (
                sanitize(self.title)
                in (sanitize(name) for name in [video.title] + video.alternative_titles)
            ):
                matches.add("title")

        return matches


class KtuvitProvider(Provider):
    """Ktuvit Provider."""

    languages = {Language(l) for l in ["heb"]}
    server_url = "https://www.ktuvit.me/"
    sign_in_url = "Services/MembershipService.svc/Login"
    search_url = "Services/ContentProvider.svc/SearchPage_search"
    movie_info_url = "MovieInfo.aspx?ID="
    episode_info_url = "Services/GetModuleAjax.ashx?"
    request_download_id_url = "Services/ContentProvider.svc/RequestSubtitleDownload"
    download_link = "Services/DownloadFile.ashx?DownloadIdentifier="
    subtitle_class = KtuvitSubtitle

    _tmdb_api_key = "a51ee051bcd762543373903de296e0a3"

    def __init__(self, email=None, hashed_password=None):
        if any((email, hashed_password)) and not all((email, hashed_password)):
            raise ConfigurationError("Email and Hashed Password must be specified")

        self.email = email
        self.hashed_password = hashed_password
        self.logged_in = False
        self.session = None
        self.loginCookie = None

    def initialize(self):
        self.session = Session()

        # login
        if self.email and self.hashed_password:
            logger.info("Logging in")

            data = {"request": {"Email": self.email, "Password": self.hashed_password}}
            
            self.session.headers['Accept-Encoding'] = 'gzip'
            self.session.headers['Accept-Language'] = 'en-us,en;q=0.5'
            self.session.headers['Pragma'] = 'no-cache'
            self.session.headers['Cache-Control'] = 'no-cache'
            self.session.headers['Content-Type'] = 'application/json'
            self.session.headers['User-Agent']: os.environ.get("SZ_USER_AGENT", "Sub-Zero/2")

            r = self.session.post(
                self.server_url + self.sign_in_url,
                json=data,
                allow_redirects=False,
                timeout=10,
            )
            
            if r.content:
                try:
                    responseContent = r.json()
                except json.decoder.JSONDecodeError:
                    AuthenticationError("Unable to parse JSON return while authenticating to the provider.")
                else:
                    isSuccess = False
                    if 'd' in responseContent:
                        responseContent = json.loads(responseContent['d'])
                        isSuccess = responseContent.get('IsSuccess', False)
                        if not isSuccess:
                            AuthenticationError("ErrorMessage: " + responseContent['d'].get("ErrorMessage", "[None]"))
                    else:
                        AuthenticationError("Incomplete JSON returned while authenticating to the provider.")

            logger.debug("Logged in")
            self.loginCookie = (
                r.headers["set-cookie"][1].split(";")[0].replace("Login=", "")
            )

            self.session.headers["Accept"]="application/json, text/javascript, */*; q=0.01"
            self.session.headers["Cookie"]="Login=" + self.loginCookie
                       
            self.logged_in = True

    def terminate(self):
        self.session.close()

    @region.cache_on_arguments(expiration_time=SHOW_EXPIRATION_TIME)
    def _search_imdb_id(self, title, year, is_movie):
        """Search the IMDB ID for the given `title` and `year`.

        :param str title: title to search for.
        :param int year: year to search for (or 0 if not relevant).
        :param bool is_movie: If True, IMDB ID will be searched for in TMDB instead of Wizdom.
        :return: the IMDB ID for the given title and year (or None if not found).
        :rtype: str

        """
        # make the search
        logger.info(
            "Searching IMDB ID for %r%r",
            title,
            "" if not year else " ({})".format(year),
        )
        category = "movie" if is_movie else "tv"
        title = title.replace("'", "")
        # get TMDB ID first
        r = self.session.get(
            "http://api.tmdb.org/3/search/{}?api_key={}&query={}{}&language=en".format(
                category,
                self._tmdb_api_key,
                title,
                "" if not year else "&year={}".format(year),
            )
        )
        r.raise_for_status()
        tmdb_results = r.json().get("results")
        if tmdb_results:
            tmdb_id = tmdb_results[0].get("id")
            if tmdb_id:
                # get actual IMDB ID from TMDB
                r = self.session.get(
                    "http://api.tmdb.org/3/{}/{}{}?api_key={}&language=en".format(
                        category,
                        tmdb_id,
                        "" if is_movie else "/external_ids",
                        self._tmdb_api_key,
                    )
                )
                r.raise_for_status()
                imdb_id = r.json().get("imdb_id")
                if imdb_id:
                    return str(imdb_id)
                else:
                    return None
        return None

    def query(
        self, title, season=None, episode=None, year=None, filename=None, imdb_id=None
    ):
        # search for the IMDB ID if needed.
        is_movie = not (season and episode)
        imdb_id = imdb_id or self._search_imdb_id(title, year, is_movie)
        if not imdb_id:
            return {}

        # search
        logger.debug("Using IMDB ID %r", imdb_id)

        query = {
            "FilmName": title,
            "Actors": [],
            "Studios": [],
            "Directors": [],
            "Genres": [],
            "Countries": [],
            "Languages": [],
            "Year": "",
            "Rating": [],
            "Page": 1,
            "SearchType": "0",
            "WithSubsOnly": False,
        }

        if not is_movie:
            query["SearchType"] = "1"

        if year:
            query["Year"] = year

        # get the list of subtitles
        logger.debug("Getting the list of subtitles")

        url = self.server_url + self.search_url
        r = self.session.post(
            url, json={"request": query}, timeout=10
        )
        r.raise_for_status()

        if r.content:
            try:
                responseContent = r.json()
            except json.decoder.JSONDecodeError:
                json.decoder.JSONDecodeError("Unable to parse JSON returned while getting Film/Series Information.")
            else:
                isSuccess = False
                if 'd' in responseContent:
                    responseContent = json.loads(responseContent['d'])
                    results = responseContent.get('Films', [])
                else:
                    json.decoder.JSONDecodeError("Incomplete JSON returned while getting Film/Series Information.")
        else:
            return  {}

        # loop over results
        subtitles = {}
        for result in results:
            imdb_link = result["IMDB_Link"]
            imdb_link = imdb_link[0: -1] if imdb_link.endswith("/") else imdb_link
            results_imdb_id = imdb_link.split("/")[-1]
            
            if results_imdb_id != imdb_id:
                logger.debug(
                    "Subtitles is for IMDB %r but actual IMDB ID is %r",
                    results_imdb_id,
                    imdb_id,
                )
                continue

            language = Language("heb")
            hearing_impaired = False
            ktuvit_id = result["ID"]
            page_link = self.server_url + self.movie_info_url + ktuvit_id

            if is_movie:
                subs = self._search_movie(ktuvit_id)
            else:
                subs = self._search_tvshow(ktuvit_id, season, episode)

            for sub in subs:
                # otherwise create it
                subtitle = KtuvitSubtitle(
                    language,
                    hearing_impaired,
                    page_link,
                    title,
                    season,
                    episode,
                    title,
                    imdb_id,
                    ktuvit_id,
                    sub["sub_id"],
                    sub["rls"],
                )
                logger.debug("Found subtitle %r", subtitle)
                subtitles[sub["sub_id"]] = subtitle

        return subtitles.values()

    def _search_tvshow(self, id, season, episode):
        subs = []

        url = (
            self.server_url
            + self.episode_info_url
            + "moduleName=SubtitlesList&SeriesID={}&Season={}&Episode={}".format(
                id, season, episode
            )
        )
        r = self.session.get(url, timeout=10)
        r.raise_for_status()

        sub_list = ParserBeautifulSoup(r.content, ["html.parser"])
        sub_rows = sub_list.find_all("tr")

        for row in sub_rows:
            columns = row.find_all("td")
            sub = {"id": id}

            for index, column in enumerate(columns):
                if index == 0:
                    sub['rls'] = column.get_text().strip().split("\n")[0]
                if index == 5:
                    sub['sub_id'] = column.find("input", attrs={"data-sub-id": True})["data-sub-id"]

            subs.append(sub)
        return subs

    def _search_movie(self, movie_id):
        subs = []
        url = self.server_url + self.movie_info_url + movie_id
        r = self.session.get(url, timeout=10)
        r.raise_for_status()

        html = ParserBeautifulSoup(r.content, ["html.parser"])
        sub_rows = html.select("table#subtitlesList tbody > tr")

        for row in sub_rows:
            columns = row.find_all("td")
            sub = {
                'id': movie_id
            }
            for index, column in enumerate(columns):
                if index == 0:
                    sub['rls'] = column.get_text().strip().split("\n")[0]
                if index == 5:
                    sub['sub_id'] = column.find("a", attrs={"data-subtitle-id": True})["data-subtitle-id"]

            subs.append(sub)
        return subs

    def list_subtitles(self, video, languages):
        season = episode = None
        year = video.year
        filename = video.name
        imdb_id = video.imdb_id

        if isinstance(video, Episode):
            titles = [video.series] + video.alternative_series
            season = video.season
            episode = video.episode
            imdb_id = video.series_imdb_id
        else:
            titles = [video.title] + video.alternative_titles
            imdb_id = video.imdb_id

        for title in titles:
            subtitles = [
                s
                for s in self.query(title, season, episode, year, filename, imdb_id)
                if s.language in languages
            ]
            if subtitles:
                return subtitles

        return []

    def download_subtitle(self, subtitle):
        if isinstance(subtitle, KtuvitSubtitle):
            downloadIdentifierRequest = {
                "FilmID": subtitle.ktuvit_id,
                "SubtitleID": subtitle.subtitle_id,
                "FontSize": 0,
                "FontColor": "",
                "PredefinedLayout": -1,
            }

            logger.debug("Download Identifier Request data: " + str(json.dumps({"request": downloadIdentifierRequest})))

            # download
            url = self.server_url + self.request_download_id_url
            r = self.session.post(
                url, json={"request": downloadIdentifierRequest}, timeout=10
            )
            r.raise_for_status()
            
            if r.content:
                try:
                    responseContent = r.json()
                except json.decoder.JSONDecodeError:
                    json.decoder.JSONDecodeError("Unable to parse JSON returned while getting Download Identifier.")
                else:
                    isSuccess = False
                    if 'd' in responseContent:
                        responseContent = json.loads(responseContent['d'])
                        downloadIdentifier = responseContent.get('DownloadIdentifier', None)

                        if not downloadIdentifier:
                            json.decoder.JSONDecodeError("Missing Download Identifier.")    
                    else:
                        json.decoder.JSONDecodeError("Incomplete JSON returned while getting Download Identifier.")

            url = self.server_url + self.download_link + downloadIdentifier

            r = self.session.get(url, timeout=10)
            r.raise_for_status()

            if not r.content:
                logger.debug(
                    "Unable to download subtitle. No data returned from provider"
                )
                return

            subtitle.content = fix_line_ending(r.content)
import requests
from bs4 import BeautifulSoup
from record import Record, OAIRecordException
import time
import sys
import utils


class OAI:
    def __init__(self, institution):
        self.url = institution.url
        self.id = institution.id
        self.id_prefix = institution.id_prefix
        self.name = institution.name or self.get_institution_name()
        self.metadata_prefix = self.get_metadata_prefix()
        self.include = institution.include
        self.exclude = institution.exclude
        self.hub = institution.hub
        self.skipped_record_messages = {}

    def print_info(self):
        print(f"Institution name: {self.name}")
        print(f"Institution ID: {self.id}")
        print(f"OAI feed URL: {self.url}")
        print(f"Metadata prefix: {self.metadata_prefix}\n")

    def oai_request(self, verb):
        """
        Instatiates an OAI feed request, given an OAI verb.

        :param verb:
        :return: BeautifulSoup object
        """
        params = {
            "verb": verb
        }
        try:
            res = requests.get(self.url, params=params)
        except requests.exceptions.MissingSchema as e:
            return False
        soup = BeautifulSoup(res.content, 'html.parser')

        return soup

    def get_metadata_prefixes(self):
        verb = "ListMetadataFormats"
        soup = self.oai_request(verb)
        if not soup:
            raise Exception("Missing schema error for: {}".format(self.url))

        metadata_prefixes = [m.getText() for m in soup.find_all('metadataprefix')]

        return metadata_prefixes

    def get_metadata_prefix(self):
        """
        Requests an OAI feed's ListMetadataFormats endpoint to find available metadata formats
        Right now, oai_dc is preferred and is returned if it's a viable option

        :return: string
        """
        metadata_prefixes = self.get_metadata_prefixes()

        # Right now we prefer oai_dc metadata
        if 'oai_dc' in metadata_prefixes:
            metadata_prefix = 'oai_dc'

        # Otherwise we just pick one and hope that it works
        # TODO: Test a bunch of different prefixes to make sure they're interoperable
        else:
            metadata_prefix = metadata_prefixes[0]

        return metadata_prefix

    def identify(self):
        """
        Return data included in the OAI feeds Identify endpoint

        :return: dict
        """
        verb = "Identify"
        soup = self.oai_request(verb)

        metadata = soup.find('identify').findAll()

        return {row.name: row.getText() for row in metadata}

    def get_institution_name(self):
        """
        For a given OAI feed, return the institution's name as stored in the OAI feed's 'Identify' endpoint.

        :return: Institution name as string
        """
        verb = "Identify"
        soup = self.oai_request(verb)
        try:
            name = soup.find('repositoryname').getText()
        except AttributeError as e:
            raise
        return name

    def list_sets(self):
        """
        For a given OAI feed, return a list of sets and set IDs

        """
        verb = "ListSets"
        soup = self.oai_request(verb)
        sets = [{"setSpec": set.find("setspec").getText(), "setName": set.find("setname").getText()} for set in soup.find_all("set")]

        return sets

    def add_skipped_record(self, reason, record):
        if reason in self.skipped_record_messages:
            self.skipped_record_messages[reason].append(record)
        else:
            self.skipped_record_messages[reason] = [record]

    def crawl(self):
        """
        Crawl an OAI feed, parse and format metadata and output it in a DPLA-formatted JSON file

        :return: metadata and count of skipped records
        """
        print(f"{self.name} ({self.id})")
        print(self.id)
        out = []
        url = self.url
        metadata_prefix = self.metadata_prefix
        resumption_token = True
        records = True
        params = {
            "verb": "ListRecords",
            "metadataPrefix": self.metadata_prefix
        }
        # TODO: If Include is a list it needs to be iterated through
        if self.include:
            params["set"] = self.include

        timeouts = 0
        server_errors = 0
        skipped = 0
        potential_urls = {}
        no_map = False

        while records and resumption_token:
            sys.stdout.write("\r{} records added : {} records skipped".format(len(out), skipped))
            sys.stdout.flush()
            # Some feeds are touchy about requesting too fast, so we pause for 5 seconds if a request error is encountered.
            try:
                res = requests.get(url, params=params, timeout=30)
            except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
                timeouts += 1
                print("\nRequest timed out. Waiting 5 seconds and trying again. Attempt {}".format(timeouts))
                time.sleep(5)
                if timeouts == 5:
                    print("\nRequest has timed out 5 times. Stopping harvest.")
                    break
                continue
            if res.status_code // 100 == 5:
                server_errors += 1
                print("\nServer error. Waiting 5 seconds and trying request again.")
                print(res.status_code)
                time.sleep(5)
                if server_errors == 5:
                    print("\nFeed has returned a server error 5 times. Stopping crawl.")
                    break
                continue

            # OAI doesn't like it if you're using a resumption token with a metadataPrefix or set param, so we delete it after initial request
            if 'metadataPrefix' in params:
                del params['metadataPrefix']
            if 'set' in params:
                del params['set']

            soup = BeautifulSoup(res.content, 'html.parser')

            records = soup.find_all('record')
            try:
                resumption_token = soup.find('resumptiontoken').getText()
            except AttributeError as e:
                resumption_token = None
            params['resumptionToken'] = resumption_token
            for record in records:
                if metadata_prefix == 'oai_dc' or metadata_prefix == 'oai_qdc':
                    metadata_prefix = '{}:dc'.format(metadata_prefix)
                oai_data = {
                    "metadata_prefix": metadata_prefix,
                    "institution": self.name,
                    "institution_id": self.id,
                    "institution_id_prefix": self.id_prefix,
                    "exclude": self.exclude,
                    "hub": self.hub,
                    "oai_url": self.url
                }

                try:
                    out_record = Record(record, oai_data)
                    mapped_out_record = out_record.map()
                    out.append(mapped_out_record)
                except OAIRecordException as e:
                    skipped += 1
                    self.add_skipped_record(e.message, e.record)
                    continue

        print(f"\n{skipped} items were skipped.")
        # utils.write_file("files/institutions/", out, self.id, self.name, skipped, self.skipped_record_messages)

        return out, skipped, self.skipped_record_messages
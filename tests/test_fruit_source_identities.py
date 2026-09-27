"""Fruit-trees P3 shipped identity rows: hashes, kinds, privacy, registry."""

import re
import sys
import unittest

from tests._harness import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "skills" / "knuaf-doc" / "scripts"))
import gg_source_identity as identities  # noqa: E402


EXPECTED_SHA256 = {
    "e3b654bfcfe70121fbbb62a951bf364a6418c649e71ec62f7ce8a1cfbe4df9f2",
    "3f6cfe3c37a75af440b5804605ecf64dfbcc393b9b22b60f832c9bebaf728851",
    "d1460be202e89e5e73acc5549e0007949119701e9376fdd57744cad71f202233",
    "40493e71df8c227143c1a5817f177ba022201abf8b7516d21648e5478afd56cb",
    "bfee726b10e3abaa57faf68e4bd8448a32f3f27caff1e3af4c94b897f7445a73",
    "f0992e5cd7560719edb0e8f1011f99691bb075c76ad652186cb32a64793bfb64",
    "bcf256901b4beeace12c3ee58cb35daf8b12e67aaddfa2cba3099cc71fe5f20b",
    "201215621df8b15a6e9ac5a0ae3a76cf0f91b3ac2d78a1fe869a124c55a16da7",
    "7231039639bb513b0bb0ac9e43fac69e30276579dbfb0f9803cd58ecd6839a9e",
    "54531e5fd0a2a40075304c52f23d6e561a4ed674ab79996d27d52ff0695da1e8",
    "649705b11c490a8212c6815d57b614804530724ea6557b3a6693a4f9eaa62a87",
    "350695cac659a0f2651ad1224d4eaa3b4862c99fd3115200609ed3348028e27d",
    "f6afa766be3f815deedf6ea445aecd7842d465222f3178f93ce495e49c201561",
    "0a0319b81d27085f7655eac0260c9928842a7ad2ba98db7a255ce8993b446fa1",
    "1ecbdcf49e2d76547de5ae16cd2c6d1c283629c11962344d9b2751a60633cd9f",
    "1d0f1487f72637b6793bf7c7f2a59e671528e8be5a218e20344c27375e476eac",
    "7d581966ba96b431831597267737be850ce5a24d7d3b58d974c0be92351308cf",
    "973c9e0ff91a77d0f3a95dac7df9c5a8e784f44ce8e082f81ac5ecd45543ea47",
    "8eeebe59181f2eea5dd51e5a2b8a4d39442485fc8e962227a00ab2a2e97c096f",
    "c8063cd0535c96afde5b76bfe30e6f3ef7b1203458c200b43cf6d339c37e0e8d",
    "818647be3a4d113fb50730848582d7c2365d7469c27989607a82a17e54545c6b",
    "4ce2f2c3a577b0c8fde5659af9d4f23b636f05961bb72e9a3c72d8fbe6b37f00",
    "bb1235ad7a0270676c08332bbf5729d4a1a50c5e919ff8906f7ef55f690daaef",
    "9383f519e86f9bfe9d2322c5d1f22e15aeea4f51e7b28a9bceee882804942d35",
    "c711f7d2bb54fd032597a62c159b90b1ff8e2d21f93fc339aded8c355011e328",
    "eef60791aee5b5731d4943f8a2aa8b214eaa8e22811cbf224e20cf7d8a17bbec",
    "ee8306dac6642d42bb8fe8cd33f412c7935dbc4bdadebddf7707dd573b87ec74",
    "4eea2f7a2e5c38d92f2e8cdb196e8704352a14b354e68bfe98fb7df67a9c05ec",
    "b4b138f497cbd748bd7f9fcf0f2e8d3d416b32c0b95f6370bd1cf08db74ec796",
    "da3267370fbb55976a1c447cdbb81cb8438d5e312b2ef416fbf56cf6c215bb1d",
    "592e09e6a8140a2d0439d9dfb197e979968d696b8af2b28f81c482bb3eb52dbc",
    "131bfb2e22e5e2a1fc7f95ad8798b4f9982c65301286e9260d20e6b4db7611ae",
    "8319d065765c6a10a4653f6831eb12040811ec35fc42b90146276445087637e6",
    "be3f2d27a1eec3d41b5dd6b7388f0c97a95f254f9ad4f270ff9a77a67d5c389c",
    "e975b60773b760d35ca7611d7570cefa3443e6e1b4893ffdb56e7995b2b0db3d",
    "24f010a2f672c3edb9bc54bac86ad2a9e7f53bb3edfe8e0507b4b80c6b00ff79",
    "56dcd29ebe89a4562a099e66f55e2acbaf44ceab84727f59f0d892059ffb12c4",
    "78fdae3f5b7508a451783b8a8e32becca1332fea11d0c6c8c1c7b0bf07d700cb",
    "0322f39a1f135203fe8f791c3161e6244bc5bef5996b9111b4c79529e535d7f5",
    "223011c1806d4b36fc2a4f8cc059317fb5c113d2c8fb831ca98d6fe4f92e883c",
    "8cd4522c55de7bb9cab2793f58c74a5185a363d1bd416e6cadff4b1fb7800a70",
}

EXPECTED_KIND_COUNTS = {
    "school_form": 1,
    "student_example": 1,
    "official_publication": 3,
    "public_no_derivatives": 12,
    "ncs_material": 12,
    "official_download": 12,
}

_DIGIT_RUN = re.compile(r"\d{6,}")


class FruitIdentitiesTests(unittest.TestCase):
    def _fruit_rows(self):
        entries = identities.load_source_identities()
        return {sha: row for sha, row in entries.items()
                if row["major"] == "fruit_trees"}

    def test_shipped_catalogue_validates_and_hashes_match(self):
        rows = self._fruit_rows()
        self.assertEqual(EXPECTED_SHA256, set(rows))
        for sha in EXPECTED_SHA256:
            with self.subTest(sha=sha):
                self.assertIsNone(rows[sha]["source_id"])

    def test_kind_counts(self):
        counts = {}
        for row in self._fruit_rows().values():
            counts[row["kind"]] = counts.get(row["kind"], 0) + 1
        self.assertEqual(EXPECTED_KIND_COUNTS, counts)

    def test_student_example_shape_and_privacy(self):
        rows = [row for row in self._fruit_rows().values()
                if row["kind"] == "student_example"]
        self.assertEqual(1, len(rows))
        bib = rows[0]["bibliographic"]
        self.assertEqual(1, len(bib["authors"]))
        self.assertIsNone(bib["publisher"])
        self.assertIsNone(bib["year"])
        self.assertIsNone(rows[0]["note"])
        for text in (bib["title"], *bib["authors"]):
            self.assertIsNone(_DIGIT_RUN.search(text))

    def test_fruit_hashes_not_in_reuse_registry(self):
        registry = identities.gg_reuse.load_registry(identities.REGISTRY_PATH)
        registry_hashes = set()
        for entry in registry.document["entries"]:
            registry_hashes.update(entry.get("source_sha256") or [])
        self.assertFalse(EXPECTED_SHA256 & registry_hashes)


if __name__ == "__main__":
    unittest.main()

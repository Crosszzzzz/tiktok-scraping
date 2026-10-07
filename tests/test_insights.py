"""Prevent private-life conclusions and source/date confusion in observations."""

import unittest

from app.analysis import analyze_records, build_markdown_report
from app.insights import extract_mentions


def record(identifier="1", **kwargs):
    item = {"id": identifier, "profile": "@example", "source": "reposts",
            "url": f"https://www.tiktok.com/@creator/video/{identifier}", "author": "creator",
            "caption": "", "published_at": None, "interaction_at": None,
            "interaction_date_source": None}
    item.update(kwargs)
    return item


class MentionTests(unittest.TestCase):
    def test_original_caption_never_becomes_reposter_or_liker_speech(self):
        for source in ("reposts", "likes"):
            with self.subTest(source=source):
                context = extract_mentions(record(source=source, caption="Mi pareja @someone"))[0]
                self.assertEqual(context["actor"], "@creator")
                self.assertEqual(context["attribution"], "original_author")
                result = analyze_records([record(source=source, caption="Mi pareja @someone")])
                self.assertEqual(result["mentions"][0]["owner_authored_count"], 0)
                self.assertNotIn("relationship", result["mentions"][0])

    def test_own_caption_is_attributed_and_self_mentions_marked(self):
        contexts = extract_mentions(record(source="uploads", caption="@Example con @friend"))
        self.assertTrue(contexts[0]["is_self"])
        self.assertFalse(contexts[1]["is_self"])
        self.assertTrue(all(context["attribution"] == "profile" for context in contexts))
        self.assertTrue(all(context["actor"] == "@example" for context in contexts))

    def test_repost_note_needs_both_verified_origin_and_matching_author(self):
        base = {"repost_note": "Para @friend: te quiero", "repost_note_author": "@EXAMPLE"}
        for provenance in ("visible", "user_verified"):
            context = extract_mentions(record(**base, repost_note_source=provenance))[0]
            self.assertEqual(context["origin"], "repost_note")
            self.assertEqual(context["attribution"], "profile")
            self.assertEqual(context["actor"], "@example")
        for provenance in (None, "estimated", ["visible"]):
            context = extract_mentions(record(**base, repost_note_source=provenance))[0]
            self.assertEqual(context["attribution"], "unverified")
            self.assertEqual(context["actor"], "")
        for author in (None, "unrelated", "Nombre de persona"):
            context = extract_mentions(record(repost_note=base["repost_note"], repost_note_author=author,
                                              repost_note_source="visible"))[0]
            self.assertEqual(context["attribution"], "unverified")

    def test_actor_normalization_accepts_account_url_but_not_unrelated_url(self):
        context = extract_mentions(record(profile="https://www.tiktok.com/@Example/",
                                          repost_note="@friend", repost_note_author="example",
                                          repost_note_source="user_verified"))[0]
        self.assertEqual(context["attribution"], "profile")
        context = extract_mentions(record(profile="https://example.com/@example/",
                                          repost_note="@friend", repost_note_author="example",
                                          repost_note_source="user_verified"))[0]
        self.assertEqual(context["attribution"], "unverified")

    def test_urls_emails_ocr_and_analyst_notes_do_not_create_identity_edges(self):
        contexts = extract_mentions(record(
            caption="hola@example.com https://tiktok.com/@link tiktok.com/@bare /@path "
                    "@friend @FRIEND. @x @" + "a" * 33,
            ocr_text="@ocr", transcript="@voice", visual_summary="@visual", notes="@analyst",
        ))
        self.assertEqual([context["handle"] for context in contexts], ["@friend"])

    def test_styled_unicode_and_case_are_normalized(self):
        contexts = extract_mentions(record(caption="Con @𝗙𝗿𝗶𝗲𝗻𝗱 y @FRIEND_2, @Friend.2"))
        self.assertEqual([context["handle"] for context in contexts], ["@friend", "@friend_2", "@friend.2"])

    def test_repetitions_and_multiple_origins_count_records_once(self):
        result = analyze_records([
            record("1", caption="@friend @friend", repost_note="@friend @other", repost_note_author="example",
                   repost_note_source="visible"),
            record("2", caption="@friend"),
        ])
        friend = next(row for row in result["mentions"] if row["handle"] == "@friend")
        self.assertEqual(friend["count"], 2)
        self.assertEqual(friend["owner_authored_count"], 1)
        self.assertEqual(friend["evidence_ids"], ["1", "2"])
        self.assertEqual(len(friend["contexts"]), 3)

    def test_conflicting_duplicate_notes_do_not_inherit_verified_authorship(self):
        result = analyze_records([
            record("1", repost_note="@friend", repost_note_author="example", repost_note_source="visible"),
            record("1", repost_note="@friend romance para siempre", repost_note_author="creator", repost_note_source="visible"),
        ])
        self.assertEqual(result["metrics"]["total_records"], 1)
        self.assertEqual(result["mentions"][0]["owner_authored_count"], 0)
        self.assertEqual(result["mentions"][0]["contexts"][0]["attribution"], "unverified")
        self.assertEqual(result["observation_summary"]["unverified_note_count"], 1)

    def test_public_notes_are_thematic_content_but_unverified_annotations_are_not(self):
        result = analyze_records([
            record("1", repost_note="Una ruptura @friend", repost_note_source="visible"),
            record("2", repost_note="Una ruptura @friend", repost_note_source=None),
        ])
        topic = next(row for row in result["topics"] if row["name"] == "Despedidas y cambios en vínculos")
        self.assertEqual(topic["evidence_ids"], ["1"])
        self.assertEqual(result["mentions"][0]["owner_authored_count"], 0)
        self.assertEqual(result["metrics"]["coverage"]["repost_note"], 1)

    def test_report_preserves_provenance_and_escapes_excerpts(self):
        markdown = build_markdown_report({"records": [record(
            caption="Mi pareja @friend <script>", repost_note="Para @friend",
            repost_note_author="example", repost_note_source="visible",
        )]})
        self.assertIn("## Menciones y procedencia", markdown)
        self.assertIn("autor original (@creator)", markdown)
        self.assertIn("nota de republicación · perfil consultado (@example)", markdown)
        self.assertIn("no vínculos personales", markdown)
        self.assertNotIn("<script>", markdown)


class ThematicPeriodTests(unittest.TestCase):
    def test_consecutive_months_make_period_with_actual_sample_denominator(self):
        result = analyze_records([
            record("1", source="uploads", caption="Despecho", published_at="2025-09-01"),
            record("2", source="uploads", caption="Receta", published_at="2025-09-02"),
            record("3", source="uploads", caption="Desamor", published_at="2025-10-01"),
            record("4", source="uploads", caption="Infidelidad en una película", published_at="2025-11-01"),
        ])
        period = next(row for row in result["thematic_periods"] if row["topic"] == "Despedidas y cambios en vínculos")
        self.assertEqual((period["start"], period["end"]), ("2025-09", "2025-11"))
        self.assertEqual(period["count"], 3)
        self.assertEqual(period["total_in_period"], 4)
        self.assertEqual(period["share"], 0.75)
        self.assertEqual(period["evidence_ids"], ["1", "3", "4"])
        self.assertIn("no establece un acontecimiento personal", period["description"])

    def test_unknown_months_split_period_without_implying_inactivity(self):
        result = analyze_records([
            record("1", source="uploads", caption="Despecho", published_at="2025-09-01"),
            record("2", source="uploads", caption="Desamor", published_at="2025-09-02"),
            record("3", source="uploads", caption="Despecho", published_at="2025-11-01"),
            record("4", source="uploads", caption="Desamor", published_at="2025-11-02"),
        ])
        periods = result["thematic_periods"]
        self.assertEqual([(row["start"], row["end"]) for row in periods], [("2025-09", "2025-09"), ("2025-11", "2025-11")])
        self.assertTrue(all(any("no demuestra ausencia" in limit for limit in row["limitations"]) for row in periods))

    def test_all_sources_and_date_bases_stay_separate(self):
        records = []
        for source in ("uploads", "reposts", "likes"):
            for number in range(2):
                records.append(record(f"{len(records) + 1}", source=source, caption="Romance",
                                      published_at="2025-01-01", interaction_at="2026-10-03",
                                      interaction_date_source="visible"))
        periods = analyze_records(records)["thematic_periods"]
        self.assertEqual(len(periods), 3)
        own = next(row for row in periods if row["source"] == "uploads")
        self.assertEqual(own["start"], "2025-01")
        self.assertEqual(own["date_basis"], "own_publication")
        self.assertTrue(all(row["count"] == 2 for row in periods))
        for period in periods:
            if period["source"] != "uploads":
                self.assertEqual(period["start"], "2026-10")
                self.assertEqual(period["date_basis"], "interaction")

    def test_publication_observation_and_unverified_dates_cannot_date_reposts(self):
        result = analyze_records([
            record("1", caption="Desamor", published_at="2025-09-01", observed_at="2026-10-03"),
            record("2", caption="Desamor", published_at="2025-10-01", interaction_at="2025-10-01"),
            record("3", caption="Desamor", interaction_at="2025-02-31", interaction_date_source="visible"),
        ])
        self.assertEqual(result["thematic_periods"], [])
        self.assertEqual(result["observation_summary"]["dated_period_records"], 0)
        self.assertEqual(result["observation_summary"]["undated_period_records"], 3)

    def test_single_evidence_and_different_profiles_do_not_form_period(self):
        result = analyze_records([
            record("1", profile="@alpha", source="uploads", caption="Romance", published_at="2025-10-01"),
            record("2", profile="@beta", source="uploads", caption="Romance", published_at="2025-10-02"),
        ])
        self.assertEqual(result["thematic_periods"], [])

    def test_dates_conflicting_across_duplicates_cannot_create_a_period(self):
        result = analyze_records([
            record("1", caption="Desamor", interaction_at="2025-10-01", interaction_date_source="visible"),
            record("1", caption="Desamor", interaction_at="2025-10-02", interaction_date_source="visible"),
            record("2", caption="Desamor", interaction_at="2025-10-03", interaction_date_source="visible"),
        ])
        self.assertEqual(result["thematic_periods"], [])
        self.assertEqual(result["observation_summary"]["dated_period_records"], 1)


class QualityAndSummaryTests(unittest.TestCase):
    def test_missing_audio_and_music_title_do_not_imply_muting_or_copyright(self):
        result = analyze_records([record(music={"title": "Copyright desamor", "author": "Artista"},
                                         screenshots=["image.png"], frames=["frame.png"])])
        quality = result["data_quality"]
        self.assertEqual(quality["audio_counts"]["not_captured"], 1)
        self.assertEqual(quality["audio_counts"]["muted_notice"], 0)
        self.assertEqual(quality["availability_counts"]["unknown"], 1)
        self.assertEqual(quality["missing_context"]["transcript"], 1)
        self.assertFalse(any(issue["code"] == "audio_muted_notice" for issue in quality["issues"]))
        self.assertNotIn("desamor", result["profile_summary"]["text"])

    def test_explicit_audio_notice_and_its_reason_remain_linked_to_evidence(self):
        reason = "TikTok indica audio eliminado por derechos de autor."
        result = analyze_records([record(availability={
            "status": "available", "audio_status": "muted_notice", "audio_reason": reason,
            "issues": [{"code": "audio_muted_notice", "message": reason, "basis": "visible_player_notice"}],
        })])
        quality = result["data_quality"]
        issue = next(row for row in quality["issues"] if row["code"] == "audio_muted_notice")
        self.assertEqual(issue["message"], reason)
        self.assertEqual(issue["basis"], "visible_player_notice")
        self.assertEqual(issue["evidence_ids"], ["1"])
        self.assertEqual(issue["count"], 1)
        self.assertEqual(quality["audio_counts"]["muted_notice"], 1)

    def test_missing_muting_reason_stays_unknown(self):
        quality = analyze_records([record(availability={"status": "partial", "audio_status": "muted_notice"})])["data_quality"]
        issue = next(row for row in quality["issues"] if row["code"] == "audio_muted_notice")
        self.assertIn("no se ha establecido la causa", issue["message"])
        self.assertNotIn("derechos", issue["message"])
        self.assertEqual(quality["record_issues"][0]["issues"][0]["code"], "audio_muted_notice")

    def test_summary_own_mention_references_exclude_third_party_caption_evidence(self):
        result = analyze_records([
            record("1", caption="@friend"),
            record("2", repost_note="@friend", repost_note_author="example", repost_note_source="visible"),
        ])
        observation = next(row for row in result["profile_summary"]["observations"] if "menciones atribuibles" in row["text"])
        self.assertEqual(observation["evidence_ids"], ["2"])
        self.assertIn("sin establecer el vínculo", observation["text"])

    def test_summary_groups_public_content_without_biographical_conclusions(self):
        result = analyze_records([
            record("1", caption="Despecho, una ruptura", interaction_at="2025-09-01", interaction_date_source="visible"),
            record("2", caption="Me engañaste, una infidelidad", interaction_at="2025-10-01", interaction_date_source="visible"),
        ])
        summary = result["profile_summary"]
        self.assertIn("temas del contenido", summary["text"])
        self.assertTrue(summary["observations"])
        self.assertTrue(all(set(row["evidence_ids"]) <= {"1", "2"} for row in summary["observations"]))
        self.assertTrue(any("no permite establecer personalidad" in row for row in summary["unknowns"]))
        markdown = build_markdown_report({"analysis": result})
        self.assertIn("## Resumen del contenido del perfil", markdown)
        self.assertIn("## Disponibilidad y contexto pendiente", markdown)


if __name__ == "__main__":
    unittest.main()

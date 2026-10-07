import unittest

from app.analysis import analyze_records, build_markdown_report


def record(identifier="1", **kwargs):
    item = {
        "id": identifier, "profile": "@example", "source": "reposts",
        "url": f"https://www.tiktok.com/@creator/video/{identifier}",
        "caption": "", "published_at": None, "interaction_at": None,
        "interaction_date_source": None, "observed_at": "2026-10-03T10:00:00-04:00",
        "first_seen_at": "2026-10-03T10:00:00-04:00",
    }
    item.update(kwargs)
    return item


class AnalysisTests(unittest.TestCase):
    def test_empty_sample_has_no_inferences_or_timeline(self):
        result = analyze_records([])
        self.assertEqual(result["metrics"]["total_records"], 0)
        self.assertEqual(result["topics"], [])
        self.assertEqual(result["timeline"], [])
        self.assertEqual(result["thematic_profile"], [])
        self.assertIn("No hay contenido", result["summary"])

    def test_publication_capture_first_seen_and_id_never_date_interactions(self):
        result = analyze_records([record(
            "7459000000000000000", caption="Una despedida y dejar ir", published_at="2025-09-12",
        )])
        self.assertEqual(result["timeline"], [])
        self.assertEqual(result["undated_count"], 1)
        self.assertEqual(result["original_timeline"][0]["period"], "2025-09")
        self.assertEqual(result["original_timeline"][0]["date_basis"], "original_publication")

    def test_interaction_date_requires_explicit_verified_provenance(self):
        result = analyze_records([
            record("1", interaction_at="2026-09-01", interaction_date_source="visible"),
            record("2", interaction_at="2026-11-01T12:00:00-04:00", interaction_date_source="user_verified"),
            record("3", interaction_at="2026-10-01", interaction_date_source=None),
            record("4", interaction_at="2026-10-01", interaction_date_source="estimated"),
            record("5", interaction_at="2026-02-31", interaction_date_source="visible"),
        ])
        self.assertEqual([row["period"] for row in result["timeline"]], ["2026-09", "2026-11"])
        self.assertTrue(all(row["date_basis"] == "interaction" for row in result["timeline"]))
        self.assertEqual(result["undated_count"], 3)
        self.assertEqual(result["metrics"]["dated_count"], 2)

    def test_deduplicates_within_source_and_preserves_cross_source_evidence(self):
        result = analyze_records([
            record("1", caption="receta"),
            record("1", caption="Una receta para cocinar una pizza"),
            record("1", source="likes", caption="Una receta para cocinar una pizza"),
        ])
        self.assertEqual(result["metrics"]["total_records"], 2)
        self.assertEqual(result["metrics"]["duplicate_count"], 1)
        self.assertEqual(result["metrics"]["source_counts"], {"likes": 1, "reposts": 1})
        topic = next(item for item in result["topics"] if item["name"] == "Comida y cocina")
        self.assertEqual(topic["count"], 2)
        self.assertEqual(topic["evidence_ids"], ["1"])

    def test_duplicate_dates_that_conflict_are_excluded(self):
        result = analyze_records([
            record("1", interaction_at="2026-09-01", interaction_date_source="visible"),
            record("1", interaction_at="2026-10-01", interaction_date_source="visible"),
            record("1", interaction_at="2026-09-01", interaction_date_source="visible"),
        ])
        self.assertEqual(result["timeline"], [])
        self.assertEqual(result["undated_count"], 1)
        self.assertTrue(any("conflictos" in item for item in result["limitations"]))

    def test_actual_publications_and_original_dates_remain_separate(self):
        result = analyze_records([record("1", source="uploads", published_at="2025-01-02", caption="Aprender guitarra")])
        self.assertEqual(result["source_comparison"][0]["source"], "uploads")
        self.assertEqual(result["timeline"], [])
        self.assertEqual(result["original_timeline"][0]["period"], "2025-01")
        self.assertEqual(result["original_timeline"][0]["source_counts"], {"uploads": 1})

    def test_own_timeline_uses_publication_dates_without_interaction_dates(self):
        result = analyze_records([
            record("1", source="uploads", published_at="2025-09-02", caption="Receta de cocina"),
            record("2", source="reposts", published_at="2025-08-03", caption="Despedida"),
            record("3", source="likes", published_at="2025-09-04", caption="Pelicula"),
            record("4", source="uploads", published_at="2025-02-31", caption="Canción"),
        ])
        self.assertEqual(result["timeline"], [])
        self.assertEqual(len(result["own_timeline"]), 1)
        month = result["own_timeline"][0]
        self.assertEqual(month["period"], "2025-09")
        self.assertEqual(month["date_basis"], "own_publication")
        self.assertEqual(month["count"], 1)
        self.assertEqual(month["evidence_ids"], ["1"])
        self.assertEqual(month["topics"][0]["name"], "Comida y cocina")
        self.assertEqual(month["topics"][0]["share"], 1)
        self.assertEqual(result["metrics"]["own_dated_count"], 1)
        self.assertEqual(result["metrics"]["own_undated_count"], 1)
        self.assertEqual(sum(item["count"] for item in result["original_timeline"]), 3)

    def test_own_changes_describe_published_content_and_only_link_own_evidence(self):
        records = [
            record("1", source="uploads", published_at="2025-09-01", caption="Receta de cocina"),
            record("2", source="uploads", published_at="2025-09-02", caption="Una pizza para cocinar"),
            record("3", source="uploads", published_at="2025-11-03", caption="Una película de cine"),
            record("4", source="uploads", published_at="2025-11-04", caption="Otra escena de película"),
            record("5", source="reposts", published_at="2025-09-05", caption="Cine y película"),
        ]
        result = analyze_records(records)
        self.assertEqual(result["changes"], [])
        self.assertTrue(result["own_changes"])
        for change in result["own_changes"]:
            self.assertLessEqual(set(change["evidence_ids"]), {"1", "2", "3", "4"})
            self.assertIn("publicaciones propias con fecha de publicación", change["description"])
            self.assertIn("no prueba un cambio personal", change["description"])
            self.assertNotIn("interacciones verificadas", change["description"])
        markdown = build_markdown_report({"records": records, "analysis": result})
        self.assertIn("## Cronología de publicaciones propias", markdown)
        self.assertIn("### Diferencias en publicaciones propias", markdown)

    def test_own_and_interaction_timelines_keep_their_distinct_dates(self):
        result = analyze_records([record(
            "1", source="uploads", published_at="2025-09-02", interaction_at="2026-10-03",
            interaction_date_source="user_verified", caption="Cocina",
        )])
        self.assertEqual(result["timeline"][0]["period"], "2026-10")
        self.assertEqual(result["own_timeline"][0]["period"], "2025-09")

    def test_no_own_timeline_is_invented_for_reposts(self):
        records = [record("1", published_at="2025-09-02", caption="Cocina")]
        result = analyze_records(records)
        self.assertEqual(result["own_timeline"], [])
        self.assertEqual(result["own_changes"], [])
        self.assertNotIn("## Cronología de publicaciones propias", build_markdown_report({"records": records}))

    def test_every_topic_and_change_has_existing_evidence(self):
        records = [
            record("1", caption="Comida y receta", interaction_at="2026-09-01", interaction_date_source="visible"),
            record("2", caption="Cocinar pizza", interaction_at="2026-09-02", interaction_date_source="visible"),
            record("3", caption="Despedida y dejar ir", interaction_at="2026-11-01", interaction_date_source="visible"),
            record("4", caption="Adiós, una ruptura", interaction_at="2026-11-02", interaction_date_source="user_verified"),
        ]
        result = analyze_records(records)
        known = {item["id"] for item in result["evidence_index"]}
        self.assertTrue(result["changes"])
        for item in result["topics"] + result["changes"]:
            self.assertTrue(item["evidence_ids"])
            self.assertLessEqual(set(item["evidence_ids"]), known)
        self.assertTrue(all("no prueba un cambio personal" in item["description"] for item in result["changes"]))
        self.assertFalse(any("2026-10" == row["period"] for row in result["timeline"]))

    def test_music_metadata_does_not_substitute_for_lyrics_or_content(self):
        result = analyze_records([record("1", music={"title": "Ruptura y tristeza", "author": "Artista"})])
        self.assertEqual(result["topics"], [])
        self.assertEqual(result["metrics"]["content_tone"], [])
        self.assertEqual(result["music"][0]["count"], 1)
        self.assertEqual(result["metrics"]["coverage"]["music"], 1)

    def test_visual_and_transcript_context_are_covered_but_notes_not_inferred(self):
        result = analyze_records([record("1", visual_summary="Un perro juega en un bosque", transcript="Tutorial para aprender", notes="Creo que tuvo una ruptura")])
        topics = {item["name"] for item in result["topics"]}
        self.assertIn("Animales y naturaleza", topics)
        self.assertIn("Estudios y aprendizaje", topics)
        self.assertNotIn("Despedidas y cambios en vínculos", topics)
        self.assertEqual(result["metrics"]["coverage"]["visual_summary"], 1)

    def test_report_links_evidence_and_reports_unknown_timing(self):
        markdown = build_markdown_report({"profile": "@example", "records": [record("1", caption="Una despedida") ]})
        self.assertIn("https://www.tiktok.com/@creator/video/1", markdown)
        self.assertIn("No hay interacciones con fecha verificable", markdown)
        self.assertIn("No se infieren personalidad", markdown)
        self.assertIn("Negaciones, ironía", markdown)

    def test_unsafe_urls_and_raw_markup_do_not_become_report_links(self):
        markdown = build_markdown_report({"records": [record("1", url="javascript:alert(1)", caption="<script>alert('x')</script> receta")]})
        self.assertNotIn("javascript:", markdown)
        self.assertNotIn("<script>", markdown)
        self.assertIn("&lt;script&gt;", markdown)

    def test_bad_record_types_and_absent_fields_are_tolerated(self):
        result = analyze_records([None, "bad", {}, {"id": "2", "music": "wrong", "caption": 5}])
        self.assertEqual(result["metrics"]["invalid_record_count"], 2)
        self.assertEqual(result["metrics"]["total_records"], 2)
        self.assertEqual(result["metrics"]["without_text_count"], 2)

    def test_malformed_provenance_is_not_a_verified_interaction(self):
        result = analyze_records([record("1", interaction_at="2026-10-01", interaction_date_source=["visible"])])
        self.assertEqual(result["timeline"], [])
        self.assertEqual(result["undated_count"], 1)

    def test_control_characters_in_urls_cannot_inject_report_content(self):
        markdown = build_markdown_report({"records": [record("1", url="https://www.tiktok.com/\n#Injected", caption="receta")]})
        self.assertNotIn("Injected", markdown)

    def test_seasonal_cinema_and_romance_are_content_topics_not_personal_attributes(self):
        result = analyze_records([
            record("1", caption="Decoración navideña y luces de Navidad"),
            record("2", caption="Una escena romántica de película, un beso entre personajes"),
        ])
        by_name = {item["name"]: item for item in result["topics"]}
        self.assertEqual(by_name["Navidad y celebraciones estacionales"]["evidence_ids"], ["1"])
        self.assertEqual(by_name["Cine y ficción"]["evidence_ids"], ["2"])
        self.assertEqual(by_name["Romance y afecto en el contenido"]["evidence_ids"], ["2"])
        self.assertFalse(any("relig" in item["name"].lower() for item in result["topics"]))
        self.assertIn("no la personalidad ni la vida privada", result["summary"])

    def test_learning_verb_alone_does_not_imply_educational_content(self):
        result = analyze_records([
            record("1", caption="Aprender a recibir, porque también eres merecedor de recibir"),
            record("2", caption="Aprender a decir adiós y dejar ir"),
            record("3", caption="Curso para aprender fotografía"),
            record("4", caption="Tutorial para aprender a programar"),
        ])
        study = next(item for item in result["topics"] if item["name"] == "Estudios y aprendizaje")
        self.assertEqual(study["evidence_ids"], ["3", "4"])
        self.assertEqual(study["count"], 2)

    def test_demo_export_is_explicitly_fictional(self):
        markdown = build_markdown_report({"is_demo": True, "records": [record("1", caption="Navidad")]})
        self.assertIn("EJEMPLO FICTICIO", markdown)
        self.assertIn("no corresponden a un perfil real", markdown)

    def test_report_includes_observed_collection_source_status(self):
        markdown = build_markdown_report({"records": [], "collection": {"method": "visible_browser", "sources": {
            "likes": {"status": "private", "reason": "TikTok indica que están ocultos."},
        }}})
        self.assertIn("Me gusta: private", markdown)
        self.assertIn("TikTok indica que están ocultos", markdown)


if __name__ == "__main__":
    unittest.main()

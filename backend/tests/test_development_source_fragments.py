from app.engine import compile_development_source_fragments


def test_dense_synthetic_note_never_silently_drops_tail_after_fragment_500() -> None:
    source = "".join(
        "纯虚构片段%04d。" % index
        for index in range(1, 502)
    ) + "纯虚构尾部关键暴露：温水水族箱。"

    fragments = compile_development_source_fragments(source)

    assert len(fragments) <= 500
    assert all(len(fragment.text) <= 4800 for fragment in fragments)
    assert "纯虚构尾部关键暴露：温水水族箱。" in "".join(
        fragment.text for fragment in fragments
    )
    assert "".join(fragment.text for fragment in fragments) == source
    assert [fragment.order for fragment in fragments] == list(range(1, len(fragments) + 1))


def test_ordinary_note_keeps_sentence_level_fragments_and_sections() -> None:
    fragments = compile_development_source_fragments(
        "现病史：纯虚构发热2天。\n暴露史：纯虚构淡水接触。"
    )

    assert [fragment.text for fragment in fragments] == [
        "现病史：纯虚构发热2天。",
        "暴露史：纯虚构淡水接触。",
    ]
    assert [fragment.section for fragment in fragments] == ["现病史", "暴露史"]

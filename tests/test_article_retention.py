from services.article_service import ArticleService


def test_zero_retention_days_keeps_the_article_archive(monkeypatch):
    monkeypatch.setenv("ARTICLE_RETENTION_DAYS", "0")

    assert ArticleService._retention_days() is None
    assert ArticleService._article_cutoff_date() is None


def test_positive_retention_days_has_a_cutoff(monkeypatch):
    monkeypatch.setenv("ARTICLE_RETENTION_DAYS", "7")

    assert ArticleService._retention_days() == 7
    assert ArticleService._article_cutoff_date() is not None


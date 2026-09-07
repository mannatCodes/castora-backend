from typing import List, Optional, Dict, Any
from fastapi import HTTPException
import json
from datetime import datetime, timedelta
from services.db_service import tracking_db, sources_db
from models.article_schemas import Article, PaginatedArticles


class ArticleService:
    """Service for managing article operations with the new database structure."""

    ARTICLE_RETENTION_DAYS = 2

    @staticmethod
    def _published_date_sort_expression(column_name: str = "ca.published_date") -> str:
        return f"""
        CASE
            WHEN {column_name} LIKE '___, __ ___ ____ __:__:__ %' THEN
                substr({column_name}, 13, 4) || '-' ||
                CASE substr({column_name}, 9, 3)
                    WHEN 'Jan' THEN '01'
                    WHEN 'Feb' THEN '02'
                    WHEN 'Mar' THEN '03'
                    WHEN 'Apr' THEN '04'
                    WHEN 'May' THEN '05'
                    WHEN 'Jun' THEN '06'
                    WHEN 'Jul' THEN '07'
                    WHEN 'Aug' THEN '08'
                    WHEN 'Sep' THEN '09'
                    WHEN 'Oct' THEN '10'
                    WHEN 'Nov' THEN '11'
                    WHEN 'Dec' THEN '12'
                END || '-' ||
                substr({column_name}, 6, 2) || ' ' ||
                substr({column_name}, 18, 8)
            ELSE {column_name}
        END
        """

    @classmethod
    def _article_cutoff_date(cls) -> str:
        return (datetime.now() - timedelta(days=cls.ARTICLE_RETENTION_DAYS)).isoformat()

    async def _purge_old_articles(self) -> None:
        """Keep only recent articles in the article store."""
        cutoff_date = self._article_cutoff_date()
        date_sort = self._published_date_sort_expression()
        old_article_ids = await tracking_db.execute_query(
            f"""
            SELECT ca.id
            FROM crawled_articles ca
            WHERE datetime({date_sort}) < datetime(?)
            """,
            (cutoff_date,),
            fetch=True,
        )
        article_ids = [article["id"] for article in old_article_ids if article.get("id")]
        if not article_ids:
            return

        placeholders = ",".join("?" for _ in article_ids)
        await tracking_db.execute_query(
            f"DELETE FROM article_categories WHERE article_id IN ({placeholders})",
            tuple(article_ids),
        )
        await tracking_db.execute_query(
            f"DELETE FROM crawled_articles WHERE id IN ({placeholders})",
            tuple(article_ids),
        )

    async def get_articles(
        self,
        page: int = 1,
        per_page: int = 10,
        source: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        search: Optional[str] = None,
        category: Optional[str] = None,
    ) -> PaginatedArticles:
        """Get articles with pagination and filtering."""
        try:
            await self._purge_old_articles()
            offset = (page - 1) * per_page
            cutoff_date = self._article_cutoff_date()
            date_sort = self._published_date_sort_expression()
            query_parts = [
                "SELECT ca.id, ca.title, ca.url, ca.published_date,",
                "COALESCE(NULLIF(ca.summary, ''), NULLIF(fe.summary, ''), NULLIF(fe.content, '')) AS summary,",
                "ca.feed_id",
                "FROM crawled_articles ca",
                "LEFT JOIN feed_entries fe ON fe.id = ca.entry_id",
                "WHERE ca.url IS NOT NULL AND ca.url != ''",
            ]
            query_parts.append(f"AND datetime({date_sort}) >= datetime(?)")
            query_params = [cutoff_date]
            if source:
                source_id_query = "SELECT id FROM sources WHERE name = ?"
                source_id_result = await sources_db.execute_query(source_id_query, (source,), fetch=True, fetch_one=True)
                if source_id_result and source_id_result.get("id"):
                    feed_ids_query = "SELECT id FROM source_feeds WHERE source_id = ?"
                    feed_ids_result = await sources_db.execute_query(feed_ids_query, (source_id_result["id"],), fetch=True)
                    if feed_ids_result:
                        feed_ids = [item["id"] for item in feed_ids_result]
                        placeholders = ",".join(["?" for _ in feed_ids])
                        query_parts.append(f"AND ca.feed_id IN ({placeholders})")
                        query_params.extend(feed_ids)
            if category:
                query_parts.append("""
                    AND EXISTS (
                        SELECT 1 FROM article_categories ac 
                        WHERE ac.article_id = ca.id AND ac.category_name = ?
                    )
                """)
                query_params.append(category.lower())
            if date_from:
                query_parts.append("AND datetime(ca.published_date) >= datetime(?)")
                query_params.append(date_from)
            if date_to:
                query_parts.append("AND datetime(ca.published_date) <= datetime(?)")
                query_params.append(date_to)
            if search:
                query_parts.append(
                    "AND (ca.title LIKE ? OR ca.summary LIKE ? OR fe.summary LIKE ? OR fe.content LIKE ?)"
                )
                search_param = f"%{search}%"
                query_params.extend([search_param, search_param, search_param, search_param])
            count_query = " ".join(query_parts).replace(
                "SELECT ca.id, ca.title, ca.url, ca.published_date, COALESCE(NULLIF(ca.summary, ''), NULLIF(fe.summary, ''), NULLIF(fe.content, '')) AS summary, ca.feed_id",
                "SELECT COUNT(*)",
            )
            total_articles = await tracking_db.execute_query(count_query, tuple(query_params), fetch=True, fetch_one=True)
            total_count = total_articles.get("COUNT(*)", 0) if total_articles else 0
            query_parts.append(f"ORDER BY datetime({date_sort}) DESC, ca.id DESC")
            query_parts.append("LIMIT ? OFFSET ?")
            query_params.extend([per_page, offset])
            articles_query = " ".join(query_parts)
            articles = await tracking_db.execute_query(articles_query, tuple(query_params), fetch=True)
            freshness_query = f"""
            SELECT
                MAX(datetime({date_sort})) as latest_published_date,
                MAX(datetime(crawled_date)) as latest_crawled_date
            FROM crawled_articles ca
            WHERE ca.url IS NOT NULL AND ca.url != ''
              AND datetime({date_sort}) >= datetime(?)
            """
            freshness = await tracking_db.execute_query(freshness_query, (cutoff_date,), fetch=True, fetch_one=True) or {}
            feed_ids = [article["feed_id"] for article in articles if article.get("feed_id")]
            source_names = {}
            if feed_ids:
                feed_ids_str = ",".join("?" for _ in feed_ids)
                source_query = f"""
                SELECT sf.id as feed_id, s.name as source_name
                FROM source_feeds sf
                JOIN sources s ON sf.source_id = s.id
                WHERE sf.id IN ({feed_ids_str})
                """
                sources_result = await sources_db.execute_query(source_query, tuple(feed_ids), fetch=True)
                source_names = {item["feed_id"]: item["source_name"] for item in sources_result}
            for article in articles:
                feed_id = article.get("feed_id")
                article["source_name"] = source_names.get(feed_id, "Unknown Source")
                article.pop("feed_id", None)
                article["categories"] = await self.get_article_categories(article["id"])
            total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 0
            has_next = page < total_pages
            has_prev = page > 1
            return PaginatedArticles(
                items=articles,
                total=total_count,
                page=page,
                per_page=per_page,
                total_pages=total_pages,
                has_next=has_next,
                has_prev=has_prev,
                latest_published_date=freshness.get("latest_published_date"),
                latest_crawled_date=freshness.get("latest_crawled_date"),
            )
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=500, detail=f"Error fetching articles: {str(e)}")

    async def get_article(self, article_id: int) -> Article:
        """Get a specific article by ID."""
        try:
            await self._purge_old_articles()
            cutoff_date = self._article_cutoff_date()
            date_sort = self._published_date_sort_expression()
            article_query = f"""
            SELECT ca.id, ca.title, ca.url, ca.published_date,
                   COALESCE(NULLIF(ca.content, ''), NULLIF(fe.content, ''), ca.raw_content) AS content,
                   COALESCE(NULLIF(ca.summary, ''), NULLIF(fe.summary, ''), NULLIF(fe.content, '')) AS summary,
                   ca.feed_id, ca.metadata, ca.ai_status
            FROM crawled_articles ca
            LEFT JOIN feed_entries fe ON fe.id = ca.entry_id
            WHERE ca.id = ?
              AND datetime({date_sort}) >= datetime(?)
            """
            article = await tracking_db.execute_query(article_query, (article_id, cutoff_date), fetch=True, fetch_one=True)
            if not article:
                raise HTTPException(status_code=404, detail="Article not found")
            if article.get("feed_id"):
                source_query = """
                SELECT s.name as source_name
                FROM source_feeds sf
                JOIN sources s ON sf.source_id = s.id
                WHERE sf.id = ?
                """
                source_result = await sources_db.execute_query(source_query, (article["feed_id"],), fetch=True, fetch_one=True)
                if source_result:
                    article["source_name"] = source_result["source_name"]
                else:
                    article["source_name"] = "Unknown Source"
            else:
                article["source_name"] = "Unknown Source"
            article.pop("feed_id", None)
            if article.get("metadata"):
                try:
                    article["metadata"] = json.loads(article["metadata"])
                except json.JSONDecodeError:
                    article["metadata"] = {}
            article["categories"] = await self.get_article_categories(article_id)
            return article
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=500, detail=f"Error fetching article: {str(e)}")

    async def get_article_categories(self, article_id: int) -> List[str]:
        """Get categories for a specific article."""
        query = """
        SELECT category_name
        FROM article_categories
        WHERE article_id = ?
        """
        categories = await tracking_db.execute_query(query, (article_id,), fetch=True)
        return [category.get("category_name", "") for category in categories]

    async def get_sources(self) -> List[str]:
        """Get active sources that have articles in the retention window."""
        await self._purge_old_articles()
        cutoff_date = self._article_cutoff_date()
        date_sort = self._published_date_sort_expression()
        feed_rows = await tracking_db.execute_query(
            f"""
            SELECT DISTINCT ca.feed_id
            FROM crawled_articles ca
            WHERE ca.feed_id IS NOT NULL
              AND ca.url IS NOT NULL
              AND ca.url != ''
              AND datetime({date_sort}) >= datetime(?)
            """,
            (cutoff_date,),
            fetch=True,
        )
        feed_ids = [row["feed_id"] for row in feed_rows if row.get("feed_id")]
        if not feed_ids:
            return []

        placeholders = ",".join("?" for _ in feed_ids)
        query = f"""
        SELECT DISTINCT s.name
        FROM sources s
        JOIN source_feeds sf ON sf.source_id = s.id
        WHERE s.is_active = 1
          AND sf.id IN ({placeholders})
        ORDER BY s.name
        """
        result = await sources_db.execute_query(query, tuple(feed_ids), fetch=True)
        return [row.get("name", "") for row in result if row.get("name")]

    async def get_categories(self) -> List[Dict[str, Any]]:
        """Get all categories with article counts."""
        await self._purge_old_articles()
        cutoff_date = self._article_cutoff_date()
        date_sort = self._published_date_sort_expression()
        query = f"""
        SELECT category_name, COUNT(DISTINCT article_id) as article_count
        FROM article_categories
        JOIN crawled_articles ca ON ca.id = article_categories.article_id
        WHERE datetime({date_sort}) >= datetime(?)
        GROUP BY category_name
        ORDER BY article_count DESC
        """
        return await tracking_db.execute_query(query, (cutoff_date,), fetch=True)


article_service = ArticleService()

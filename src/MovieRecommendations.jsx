import { useEffect, useState } from 'react';

export default function MovieRecommendations({
  apiBase,
  onMovieSearch,
  searchLoading,
}) {
  const [movies, setMovies] = useState([]);
  const [status, setStatus] = useState('loading');
  const [retry, setRetry] = useState(0);
  const [sort, setSort] = useState('latest');

  useEffect(() => {
    let active = true;
    let timer;
    let controller;
    let hasLoaded = false;
    setStatus('loading');

    async function loadMovies() {
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 20000);
      try {
        const response = await fetch(
          `${apiBase}/api/recommended-movies?sort=${sort}`,
          { signal: controller.signal, cache: 'no-store' },
        );
        if (!response.ok) throw new Error('추천 목록 조회 실패');
        const data = await response.json();
        if (!Array.isArray(data.movies)) throw new Error('영화 목록 형식 오류');
        if (!active) return;
        setMovies(
          data.movies
            .filter((movie) => sort === 'latest'
              ? Boolean(movie.release_date)
              : Number.isInteger(movie.audience) && movie.audience > 0)
            .slice(0, 10),
        );
        hasLoaded = true;
        setStatus('success');
      } catch (error) {
        // Keep the visible list during a temporary background refresh failure.
        if (active && !hasLoaded) setStatus('error');
      } finally {
        clearTimeout(timeout);
        if (active) timer = setTimeout(loadMovies, 60000);
      }
    }

    loadMovies();
    return () => {
      active = false;
      clearTimeout(timer);
      controller?.abort();
    };
  }, [apiBase, retry, sort]);

  return (
    <section
      className="movie-recommendations"
      aria-labelledby="movie-recommendations-title"
    >
      <div className="movie-recommendations-heading">
        <h2 id="movie-recommendations-title"><span className="recommendations-heading-icon" aria-hidden="true">▣</span> 영화 추천 리스트 <small>{sort === 'latest' ? '최신 개봉 TOP 10' : '누적 관객수 TOP 10'}</small></h2>
        <div className="movie-sort-options">
          <button
            type="button"
            aria-pressed={sort === 'latest'}
            onClick={() => setSort('latest')}
          >
            최신순
          </button>
          <button
            type="button"
            aria-pressed={sort === 'popular'}
            onClick={() => setSort('popular')}
          >
            인기순
          </button>
        </div>
      </div>

      {status === 'loading' && (
        <p role="status">추천 영화를 불러오는 중입니다.</p>
      )}

      {status === 'error' && (
        <div role="alert">
          <p>추천 영화 목록을 불러오지 못했습니다.</p>
          <button type="button" onClick={() => setRetry((value) => value + 1)}>
            다시 불러오기
          </button>
        </div>
      )}

      {status === 'success' && (
        <>
          <p>
            {sort === 'latest'
            ? '개봉일이 확인된 영화의 최신순 목록입니다.'
            : '국내 누적 관객 수가 수집된 영화의 인기순 목록입니다.'}
          </p>
          {movies.length === 0 && (
            <p>{sort === 'latest' ? '등록된 개봉일 정보가 없습니다.' : '등록된 관객 수 정보가 없습니다.'}</p>
          )}

          <div className="movie-recommendations-grid">
            {movies.map((movie, index) => (
              <button
                type="button"
                className="movie-recommendation-card"
                key={movie.title}
                disabled={searchLoading}
                onClick={() => onMovieSearch(movie.title)}
              >
                {sort === 'popular' && (
                  <span className="movie-rank">{index + 1}위</span>
                )}
                <strong>{movie.title}</strong>
                <span>개봉 {movie.release_date}</span>
                {sort === 'popular' && (
                  <>
                    <span>
                      국내 누적 {movie.audience.toLocaleString('ko-KR')}명
                    </span>
                    <span>집계 기준 {movie.audience_as_of}</span>
                  </>
                )}
                <span>촬영지 보기 →</span>
              </button>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
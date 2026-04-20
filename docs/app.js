/**
 * Boston Dan's Hub — Frontend App
 * Fetches daily_output.json and renders all sections
 */

const DATA_URL = './data/daily_output.json';

/**
 * Fetch and render the daily output
 */
async function init() {
    try {
        const response = await fetch(DATA_URL);
        if (!response.ok) {
            throw new Error(`Failed to fetch: ${response.status}`);
        }
        const data = await response.json();
        render(data);
    } catch (error) {
        console.error('Error loading daily output:', error);
        showError('Unable to load Dan\'s commentary. Please try again later.');
    }
}

/**
 * Render all sections
 */
function render(data) {
    const isFallback = detectFallback(data);

    // Show/hide fallback banner
    const fallbackBanner = document.getElementById('fallback-banner');
    if (isFallback) {
        fallbackBanner.style.display = 'block';
    }

    // Render each section
    renderMorningBrew(data.morning_brew);
    renderTrendWatch(data.trend_watch);
    renderNewsDigest(data.news_digest);
    renderBoxScores(data.box_scores);
    renderSchedule(data.schedule);
    renderTimestamp();
}

/**
 * Detect if fallback content is being used
 */
function detectFallback(data) {
    return (
        data.morning_brew &&
        data.morning_brew[0] &&
        data.morning_brew[0].includes("takin' the mornin' off")
    );
}

/**
 * Render morning brew section
 */
function renderMorningBrew(brewArray) {
    const container = document.getElementById('morning-brew');
    if (!brewArray || brewArray.length === 0) {
        container.innerHTML = '<p style="color: var(--text-muted);">No commentary today.</p>';
        return;
    }

    container.innerHTML = brewArray
        .map(paragraph => `<p>${escapeHtml(paragraph)}</p>`)
        .join('');
}

/**
 * Render trend watch section
 */
function renderTrendWatch(trends) {
    const section = document.getElementById('trend-watch-section');
    const container = document.getElementById('trend-watch');

    if (!trends || trends.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';
    container.innerHTML = trends
        .map(trend => `
            <div class="trend-card">
                <div class="trend-category">${escapeHtml(trend.category)}</div>
                <div class="trend-player">${escapeHtml(trend.player)}</div>
                <div class="trend-text">${escapeHtml(trend.trend)}</div>
                <div class="trend-take">"${escapeHtml(trend.dans_take)}"</div>
            </div>
        `)
        .join('');
}

/**
 * Render news digest section
 */
function renderNewsDigest(news) {
    const section = document.getElementById('news-digest-section');
    const container = document.getElementById('news-digest');

    if (!news || news.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';
    container.innerHTML = news
        .map(item => `
            <div class="news-item">
                <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer" class="news-headline">
                    ${escapeHtml(item.headline)}
                </a>
                <div class="news-take">${escapeHtml(item.dans_take)}</div>
            </div>
        `)
        .join('');
}

/**
 * Render box scores section
 */
function renderBoxScores(scores) {
    const section = document.getElementById('box-scores-section');
    const container = document.getElementById('box-scores');

    if (!scores || Object.keys(scores).length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';
    const cards = Object.entries(scores)
        .filter(([_, scoreData]) => scoreData && scoreData.played)
        .map(([team, scoreData]) => {
            const homeWon = scoreData.home_score > scoreData.away_score;
            return `
                <div class="score-card">
                    <div class="score-sport">${escapeHtml(scoreData.sport || 'Game')}</div>
                    <div class="score-teams">
                        <div class="score-team ${homeWon ? 'winner' : ''}">
                            ${escapeHtml(scoreData.home_team)} ${escapeHtml(scoreData.home_score)}
                        </div>
                        <div class="score-team ${!homeWon ? 'winner' : ''}">
                            ${escapeHtml(scoreData.away_team)} ${escapeHtml(scoreData.away_score)}
                        </div>
                    </div>
                    <div class="score-final">${escapeHtml(scoreData.game_date || '')}</div>
                </div>
            `;
        });

    if (cards.length === 0) {
        section.style.display = 'none';
        return;
    }

    container.innerHTML = cards.join('');
}

/**
 * Render schedule section
 */
function renderSchedule(schedule) {
    const section = document.getElementById('schedule-section');
    const container = document.getElementById('schedule');

    if (!schedule || schedule.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';
    container.innerHTML = schedule
        .map(game => `
            <div class="schedule-item">
                <div class="schedule-date">${escapeHtml(game.date)}</div>
                <div class="schedule-matchup">${escapeHtml(game.matchup)}</div>
                <div class="schedule-time">${escapeHtml(game.time_et)}</div>
            </div>
        `)
        .join('');
}

/**
 * Render last-updated timestamp
 */
function renderTimestamp() {
    const element = document.getElementById('updated-time');
    const now = new Date();
    const formatted = now.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        hour12: true,
        timeZone: 'America/New_York'
    });
    element.textContent = `Updated: ${formatted} ET`;
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Show error message
 */
function showError(message) {
    const container = document.querySelector('main');
    container.innerHTML = `
        <div class="error">
            <h2>❌ Oops</h2>
            <p>${escapeHtml(message)}</p>
        </div>
    `;
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', init);

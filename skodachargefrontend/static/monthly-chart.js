(function () {
    var el = document.getElementById('monthlyEfficiencyChart');
    if (!el) return;

    function parseAttr(name) {
        try {
            var v = el.getAttribute(name);
            return v ? JSON.parse(v) : [];
        } catch (e) {
            return [];
        }
    }

    var labels = parseAttr('data-labels');
    var estimated = parseAttr('data-estimated');
    var actual = parseAttr('data-actual');

    function showMessage(text) {
        var parent = el.parentElement || document.body;
        var msg = document.createElement('div');
        msg.textContent = text;
        msg.style.color = '#fff';
        msg.style.textAlign = 'center';
        msg.style.marginTop = '8px';
        parent.appendChild(msg);
    }

    if (!window.Chart) {
        showMessage('Chart failed to load (network/CSP?).');
        return;
    }

    var ctx = el.getContext('2d');
    try {
        new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Range-Based Efficiency (km @ 100%)',
                        data: estimated,
                        borderColor: 'rgb(100, 200, 255)',
                        backgroundColor: 'rgba(100, 200, 255, 0.05)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.3,
                        pointRadius: 4,
                        pointBackgroundColor: 'rgb(100, 200, 255)'
                    },
                    {
                        label: 'Mileage-Based Efficiency (km @ 100%)',
                        data: actual,
                        borderColor: 'rgb(144, 238, 144)',
                        backgroundColor: 'rgba(144, 238, 144, 0.05)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.3,
                        pointRadius: 4,
                        pointBackgroundColor: 'rgb(144, 238, 144)'
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: {
                        labels: { color: 'rgb(255, 255, 255)', font: { size: 12 }, padding: 15 }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 600,
                        ticks: { color: 'rgb(255, 255, 255)' },
                        grid: { color: 'rgba(255, 255, 255, 0.1)' }
                    },
                    x: {
                        ticks: { color: 'rgb(255, 255, 255)', maxRotation: 45, minRotation: 45 },
                        grid: { color: 'rgba(255, 255, 255, 0.1)' }
                    }
                }
            }
        });
    } catch (e) {
        showMessage('Chart render error: ' + e.message);
    }
})();
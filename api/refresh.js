// Vercel serverless function: triggers the daily.yml workflow_dispatch
// on GitHub so the user can refresh the dashboard from the dashboard itself.

const REPO = 'sereneleparfumteam-glitch/serene-dashboard';
const WORKFLOW = 'daily.yml';
const REF = 'main';

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const token = process.env.GH_DISPATCH_TOKEN;
  if (!token) {
    return res.status(500).json({
      error: 'Server missing GH_DISPATCH_TOKEN. Add it in Vercel env vars.',
    });
  }

  try {
    const ghRes = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Accept': 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'User-Agent': 'serene-dashboard-refresh',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ref: REF }),
      }
    );

    if (ghRes.status === 204) {
      return res.status(200).json({
        ok: true,
        message: 'Workflow disparado. El dashboard se actualizará en ~2 min.',
        runs_url: `https://github.com/${REPO}/actions/workflows/${WORKFLOW}`,
      });
    }

    const body = await ghRes.text();
    return res.status(ghRes.status).json({
      ok: false,
      error: `GitHub respondió ${ghRes.status}`,
      detail: body.slice(0, 500),
    });
  } catch (err) {
    return res.status(500).json({ ok: false, error: String(err) });
  }
};

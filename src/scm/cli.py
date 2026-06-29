"""SCM CLI — Skill Context Manager command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .indexer import SkillIndexer
from .retriever import SkillRetriever
from .session import SessionTracker
from .optimizer import SkillOptimizer
from .feedback import FeedbackEngine, FeedbackRecord
from .tracker import UsageTracker


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scm",
        description="Skill Context Manager — Context-aware skill selection for AI agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  scm index --dir ~/.hermes/skills/
  scm query "deploy application to kubernetes" --top 3
  scm session start --id dev-session-1
  scm session use --skill kubernetes-deploy --query "deploy app"
  scm optimize --dir ~/.hermes/skills/ --dry-run
  scm feedback stats
  scm insights
""")

    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── index ──
    p_index = sub.add_parser("index", help="Index skills from directories")
    p_index.add_argument("--dir", type=str, default=None,
                         help="Directory containing skills (default: auto-detect agent dirs)")
    p_index.add_argument("--all", action="store_true",
                         help="Index all known agent skill directories")
    p_index.add_argument("--recursive", action="store_true", default=True,
                         help="Scan subdirectories (default: true)")
    p_index.add_argument("--no-recursive", dest="recursive", action="store_false",
                         help="Don't scan subdirectories")

    # ── query ──
    p_query = sub.add_parser("query", help="Search for relevant skills")
    p_query.add_argument("query", nargs="*", help="Task description")
    p_query.add_argument("--query", dest="query_kw", type=str, help="Task description (alt)")
    p_query.add_argument("--top", type=int, default=5, help="Number of results (default: 5)")
    p_query.add_argument("--method", choices=["bm25", "rrf"],
                         default="rrf", help="Search method (default: rrf)")
    p_query.add_argument("--format", choices=["text", "json"], default="text",
                         help="Output format (default: text)")
    p_query.add_argument("--session", type=str, default="",
                         help="Session ID for session-aware boosting")

    # ── session ──
    p_session = sub.add_parser("session", help="Manage skill usage sessions")
    p_session_sub = p_session.add_subparsers(dest="session_action", required=True)

    p_ss_start = p_session_sub.add_parser("start", help="Start a new session")
    p_ss_start.add_argument("--id", type=str, required=True, help="Session identifier")

    p_ss_end = p_session_sub.add_parser("end", help="End a session")
    p_ss_end.add_argument("--id", type=str, required=True, help="Session identifier")

    p_ss_use = p_session_sub.add_parser("use", help="Record skill usage")
    p_ss_use.add_argument("--skill", type=str, required=True, help="Skill name")
    p_ss_use.add_argument("--id", type=str, default="", help="Session identifier")
    p_ss_use.add_argument("--query", type=str, default="", help="Task description")
    p_ss_use.add_argument("--success", type=str, default=None,
                          choices=["true", "false", "1", "0", "yes", "no"],
                          help="Was the skill effective?")

    p_ss_ctx = p_session_sub.add_parser("context", help="Get session context")
    p_ss_ctx.add_argument("--id", type=str, default="", help="Session identifier")
    p_ss_ctx.add_argument("--query", type=str, default="", help="Current task")

    # ── optimize ──
    p_opt = sub.add_parser("optimize", help="Optimize skill metadata")
    p_opt.add_argument("--dir", type=str, default=".",
                       help="Skills directory (default: .)")
    p_opt.add_argument("--dry-run", action="store_true", default=True,
                       help="Preview only (default: true)")
    p_opt.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                       help="Apply changes")

    # ── feedback ──
    p_fb = sub.add_parser("feedback", help="Record or view feedback")
    p_fb_sub = p_fb.add_subparsers(dest="feedback_action", required=True)

    p_fb_rec = p_fb_sub.add_parser("record", help="Record feedback")
    p_fb_rec.add_argument("--query", type=str, required=True, help="Task description")
    p_fb_rec.add_argument("--skill", type=str, required=True, help="Skill name")
    p_fb_rec.add_argument("--success", type=str, default="true",
                          choices=["true", "false", "1", "0", "yes", "no"],
                          help="Was it effective?")
    p_fb_rec.add_argument("--rating", type=int, default=None,
                          choices=range(1, 6),
                          help="User rating 1-5")

    p_fb_sub.add_parser("stats", help="View feedback statistics")

    # ── stats ──
    sub.add_parser("stats", help="Show indexing statistics")

    # ── clean-models ──
    p_clean = sub.add_parser("clean-models", help="Remove local model caches (no longer needed)")

    # ── insights ──
    p_ins = sub.add_parser("insights", help="Show usage insights")
    p_ins.add_argument("--days", type=int, default=30, help="Days to analyze")

    # ── mcp ──
    p_mcp = sub.add_parser("mcp", help="MCP server management")
    p_mcp_sub = p_mcp.add_subparsers(dest="mcp_action", required=True)

    from .mcp_setup import PLATFORMS
    p_mcp_setup = p_mcp_sub.add_parser("setup", help="Configure SCM MCP for agent platforms")
    for key, plat in PLATFORMS.items():
        p_mcp_setup.add_argument(f"--{key}", action="store_true",
                                 help=f"Configure for {plat.display}")
    p_mcp_setup.add_argument("--all", action="store_true",
                             help="Configure for all detected agents (auto-detect which are installed)")
    p_mcp_setup.add_argument("--force-all", action="store_true", dest="force_all",
                             help="Configure for ALL 13 agents regardless of detection")
    p_mcp_setup.add_argument("--list", action="store_true", dest="list_platforms",
                             help="List all supported agent platforms with detection status")
    p_mcp_setup.add_argument("--uninstall", action="store_true", help="Remove SCM MCP config")

    p_mcp_start = p_mcp_sub.add_parser("start", help="Start MCP server")
    p_mcp_start.add_argument("--http", action="store_true", help="HTTP/SSE mode")
    p_mcp_start.add_argument("--port", type=int, default=8321, help="Port (default: 8321)")

    p_mcp_sub.add_parser("status", help="Check SCM MCP configuration status")

    return parser


def cli():
    parser = _build_parser()
    if len(sys.argv) < 2:
        parser.print_help()
        return

    args = parser.parse_args()

    commands = {
        "index": cmd_index,
        "query": cmd_query,
        "session": cmd_session,
        "optimize": cmd_optimize,
        "feedback": cmd_feedback,
        "stats": cmd_stats,
        "clean-models": cmd_clean_models,
        "insights": cmd_insights,
        "mcp": cmd_mcp,
    }

    commands[args.command](args)


# ── Commands ─────────────────────────────────────────────────────

def cmd_index(args):
    indexer = SkillIndexer()

    # Determine which directories to scan
    if args.dir:
        directories: list[Path] = [Path(args.dir)]
    elif args.all or args.dir is None:
        directories = SkillIndexer.detect_skill_dirs()
        if not directories:
            print("No agent skill directories found. Use --dir to specify one.")
            return
    else:
        directories = [Path(".")]

    total_count = 0
    for directory in directories:
        print(f"📂 Scanning {directory} for skills...")

        def show_progress(count, total):
            if count < total:
                print(f"   ... scanned {count}/{total}", end="\r", flush=True)

        count = indexer.index_directory(
            directory,
            recursive=args.recursive,
            progress_callback=show_progress,
        )
        total_count += count

    if total_count:
        print(f"\n✅ Indexed {total_count} skill files")
    stats = indexer.stats()
    print(f"📊 Total: {stats['total_skills']} skills | {stats['total_tokens_metadata']} meta tokens | "
          f"{stats['total_tokens_body']} body tokens")
    if stats['categories']:
        print(f"📁 Categories: {', '.join(stats['categories'].keys())}")


def cmd_query(args):
    # Combine positional and --query keyword args
    query = args.query_kw or " ".join(args.query) if args.query else ""
    if not query:
        print("❌ Please provide a query. Usage: scm query \"your query\"")
        return

    retriever = SkillRetriever()

    start = time.time()

    if args.method == "bm25":
        results = retriever.bm25_search(query, top_k=args.top * 4)
    else:
        results = retriever.rrf_search(query, top_k=args.top * 4)

    if args.session:
        session_tracker = SessionTracker()
        recent = session_tracker.get_recent_skills(args.session)
        if recent:
            results = retriever.apply_session_boost(results, recent, boost=0.5)

    feedback = FeedbackEngine()
    results = feedback.apply_weights(results)

    elapsed = (time.time() - start) * 1000

    tracker = UsageTracker()
    for r in results[:args.top]:
        tracker.record_event(
            skill_name=r.skill.name, query=query,
            retrieval_method=r.retrieval_method, score=r.score,
            tokens_saved=r.skill.token_cost_body,
        )

    if args.format == "json":
        output = {
            "query": query,
            "results": [
                {
                    "name": r.skill.name, "description": r.skill.description,
                    "score": r.score, "method": r.retrieval_method,
                    "category": r.skill.category, "tags": r.skill.tags,
                    "token_cost_metadata": r.skill.token_cost_metadata,
                }
                for r in results[:args.top]
            ],
            "latency_ms": round(elapsed, 1),
            "total_candidates": len(results),
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"\n🔍 Top {min(args.top, len(results))} skills for: \"{query}\"")
        print(f"   ({elapsed:.0f}ms | {len(results)} candidates scanned)\n")
        for i, r in enumerate(results[:args.top], 1):
            tag_str = f" [{', '.join(r.skill.tags[:2])}]" if r.skill.tags else ""
            print(f"  {i}. {r.skill.name}")
            print(f"     {r.skill.description}{tag_str}")
            score_pct = int(r.score * 100)
            score_bar = "█" * min(score_pct // 10, 10) + "░" * max(10 - score_pct // 10, 0)
            print(f"     [{score_bar}] {score_pct}% | {r.retrieval_method} | "
                  f"~{r.skill.token_cost_metadata}t meta / ~{r.skill.token_cost_body}t body\n")

        total_meta = sum(r.skill.token_cost_metadata for r in results[:args.top])
        total_body = sum(r.skill.token_cost_body for r in results[:args.top])
        print(f"📊 Token cost: ~{total_meta} meta | ~{total_body} body (if loaded)")
        print(f"   💡 SCM loaded only ~{total_meta + 50} tokens instead of ~{total_body}")


def cmd_session(args):
    tracker = SessionTracker()

    if args.session_action == "start":
        session = tracker.start_session(args.id)
        print(f"✅ Session started: {args.id}")
        print(f"   Started at: {session.started_at}")

    elif args.session_action == "end":
        tracker.end_session(args.id)
        recent = tracker.get_recent_skills(args.id)
        print(f"✅ Session ended: {args.id}")
        if recent:
            print(f"   Skills used: {', '.join(recent)}")

    elif args.session_action == "use":
        if not args.skill or not args.skill.strip():
            print("❌ --skill is required. Usage: scm session use --skill <name>")
            sys.exit(1)
        session = tracker.get_or_resolve_session(args.id)
        if not session:
            print("❌ No active session. Use 'scm session start --id <id>' first, or pass --id")
            sys.exit(1)
        sid = session.session_id
        success = None
        if args.success is not None:
            success = args.success.lower() in ("true", "1", "yes")
        tracker.record_skill_use(args.skill, args.query, success, sid)
        print(f"✅ Recorded: {args.skill} in session {sid}")
        if success is not None:
            action = "effective" if success else "ineffective"
            succ_str = "true" if success else "false"
            print(f"   Tip: also run 'scm feedback record --skill {args.skill} "
                  f"--query \"{args.query}\" --success {succ_str}' "
                  f"to teach SCM this skill was {action} for this query")

    elif args.session_action == "context":
        session = tracker.get_or_resolve_session(args.id)
        if not session:
            print("❌ No session specified and no active session")
            sys.exit(1)
        sid = session.session_id
        context = tracker.optimize_skill_context(sid, args.query)
        if args.query:
            retriever = SkillRetriever()
            results = retriever.hybrid_search(args.query, top_k=3)
            context["matching_skills"] = [
                {"name": r.skill.name, "description": r.skill.description}
                for r in results
            ]
        print(json.dumps(context, indent=2))
        print("\n📝 Add this to your agent's system prompt:")
        if context["active_skills"]:
            print(f'   "Skills active in this session: {", ".join(context["active_skills"])}"')
        if context.get("matching_skills"):
            related = ", ".join(s["name"] for s in context["matching_skills"])
            print(f'   "Related skills: {related}"')
        print(f"   (~{context['context_size_tokens']} tokens)")


def cmd_optimize(args):
    directory = Path(args.dir)
    optimizer = SkillOptimizer()
    print(f"{'🔍' if args.dry_run else '⚡'} "
          f"{'Analyzing' if args.dry_run else 'Optimizing'} {directory}...")

    results = optimizer.optimize_directory(directory, dry_run=args.dry_run)
    changed = [r for r in results if r.get("changed")]
    errors = [r for r in results if "error" in r]

    print(f"\n✅ {'Analyzed' if args.dry_run else 'Optimized'} {len(results)} skills")
    print(f"   Changed: {len(changed)}")
    if errors:
        print(f"   Errors: {len(errors)}")

    if changed and args.dry_run:
        print("\n📊 Potential savings:")
        before = sum(r["before_tokens"] for r in changed)
        after = sum(r["after_tokens"] for r in changed)
        pct = ((before - after) / before * 100) if before else 0
        print(f"   Before: {before} meta tokens")
        print(f"   After:  {after} meta tokens")
        print(f"   Saved:  {before - after} tokens per load ({pct:.0f}%)")
        print("\n   Run with --no-dry-run to apply changes.\n")
        for r in sorted(changed, key=lambda x: x["before_tokens"] - x["after_tokens"],
                        reverse=True)[:5]:
            saved = r["before_tokens"] - r["after_tokens"]
            if saved > 0:
                print(f"     • {r['name']}: {r['before_tokens']}→{r['after_tokens']}t "
                      f"(saved {saved}t)")

    elif errors:
        for e in errors[:3]:
            print(f"   ⚠️  {e['name']}: {e['error']}")


def cmd_feedback(args):
    engine = FeedbackEngine()

    if args.feedback_action == "record":
        success = args.success.lower() in ("true", "1", "yes")
        engine.record(FeedbackRecord(
            query=args.query, skill_name=args.skill,
            success=success, user_rating=args.rating,
        ))
        print(f"✅ Feedback recorded: {args.skill} {'✓' if success else '✗'} for \"{args.query}\"")

    elif args.feedback_action == "stats":
        stats = engine.get_stats()
        print("\n📊 Feedback Statistics")
        print(f"   Total feedback:    {stats['total_feedback']}")
        print(f"   Success rate:      {stats['success_rate']:.0%}")
        print(f"   Query patterns:    {stats['query_patterns']}")
        print(f"   Skills with data:  {stats['skills_with_feedback']}")
        if stats['top_skills']:
            print("\n   Top skills by success rate:")
            for s in stats['top_skills'][:5]:
                rate = s['rate']
                print(f"     • {s['name']}: {s['successes']}/{s['successes'] + s['failures']} "
                      f"({rate:.0%})")


def cmd_stats(args=None):
    indexer = SkillIndexer()
    stats = indexer.stats()

    print("\n📊 Skill Index Statistics")
    print(f"   Total skills:     {stats['total_skills']}")
    print(f"   Categories:       {len(stats['categories'])}")
    print(f"   Metadata tokens:  {stats['total_tokens_metadata']}")
    print(f"   Body tokens:      {stats['total_tokens_body']}")

    if stats['categories']:
        print("\n   By category:")
        for cat, count in sorted(stats['categories'].items(), key=lambda x: -x[1])[:10]:
            print(f"     • {cat}: {count} skills")

    feedback = FeedbackEngine()
    fb_stats = feedback.get_stats()
    if fb_stats['total_feedback'] > 0:
        print(f"\n   Feedback: {fb_stats['total_feedback']} records, "
              f"{fb_stats['success_rate']:.0%} success rate")


def cmd_clean_models(args=None):
    """Remove local model caches (no longer needed in v0.8)."""
    import shutil
    models_dir = Path.home() / ".scm" / "models"
    if models_dir.exists():
        try:
            shutil.rmtree(models_dir)
            print(f"✅ Removed {models_dir}")
        except Exception as e:
            print(f"⚠️  Could not remove {models_dir}: {e}")
    else:
        print(f"✓ No model cache at {models_dir}")

    # Also clean any onnx model files in db dir
    import glob
    onnx_files = list(Path.home().glob(".scm/db/*.onnx"))
    for f in onnx_files:
        f.unlink()
        print(f"✅ Removed {f}")

    print("✅ v0.8: no local models needed. All search is BM25 + graph.")


def cmd_insights(args):
    tracker = UsageTracker()
    insights = tracker.get_insights(days=args.days)

    print(f"\n📈 Usage Insights (last {insights['period_days']} days)")
    print(f"   Total queries:     {insights['total_queries']}")
    print(f"   Tokens saved:      ~{insights['tokens_saved_estimate']:,}")
    print(f"   Retrieval methods: {insights['retrieval_methods']}")

    if insights['top_skills']:
        print("\n   Top skills used:")
        for s in insights['top_skills'][:5]:
            print(f"     • {s['name']}: {s['count']} times")

    if insights['unused_skills']:
        count = len(insights['unused_skills'])
        sample = ', '.join(insights['unused_skills'][:5])
        print(f"\n   Unused skills ({count}): {sample}{'...' if count > 5 else ''}")


def cmd_mcp(args):
    """Handle MCP server management commands."""
    if args.mcp_action == "setup":
        _mcp_setup(args)
    elif args.mcp_action == "start":
        _mcp_start(args)
    elif args.mcp_action == "status":
        _mcp_status()


_STATUS_ICON = {
    "added": "✅", "updated": "🔄", "exists": "✓", "removed": "🗑️",
    "not_found": "—", "error": "❌",
}


def _mcp_setup(args):
    """Configure SCM MCP for one or more agent platforms (registry-driven)."""
    from . import mcp_setup as ms

    if getattr(args, "list_platforms", False):
        detected = set(ms.detected_keys())
        print("\nSupported agents (✓ = detected on this system):\n")
        for key, plat in ms.PLATFORMS.items():
            marker = "✓" if key in detected else "·"
            note = f"  ({plat.note})" if plat.note else ""
            print(f"   {marker} --{key:<16} {plat.display}{note}")
            print(f"     {'':16}   {plat.path}")
        print(f"\n{len(detected)}/{len(ms.ALL_KEYS)} agents detected.")
        print("Use --all (detected only) or --force-all (all 13).")
        print()
        return

    force_all = getattr(args, "force_all", False)
    if force_all:
        targets = ms.ALL_KEYS
    elif args.all:
        if args.uninstall:
            # Uninstall: clean all regardless of detection (need to undo what was configured)
            targets = ms.ALL_KEYS
        else:
            targets = ms.detected_keys()
            if not targets:
                print("No agents detected on this system.")
                print("Use --force-all to configure all 13 anyway, or --list to see status.")
                return
            print(f"Detected {len(targets)} agent(s): {', '.join(targets)}")
    else:
        targets = [k for k in ms.PLATFORMS if getattr(args, k.replace("-", "_"), False)]

    if not targets:
        print("No targets specified. Use --all, --<agent>, or --list to see options.")
        print("Example: scm mcp setup --claude-code --cursor")
        return

    results = ms.configure_many(targets, uninstall=args.uninstall)
    verb = "Removed" if args.uninstall else "Configured"
    print(f"\nSCM MCP — {verb}\n")
    ok = 0
    for r in results:
        icon = _STATUS_ICON.get(r["status"], "•")
        print(f"   {icon} {r['display']}: {r['status']}")
        print(f"      {r['path']}")
        if r.get("error"):
            print(f"      error: {r['error']}")
        if r["status"] in ("added", "updated", "removed", "exists"):
            ok += 1
    print(f"\n{ok}/{len(results)} platform(s) {verb.lower()}.")
    if not args.uninstall and ok:
        print("Restart the agent(s) to pick up the SCM MCP server.")


def _mcp_start(args):
    """Start the SCM MCP server."""
    cmd = [sys.executable, "-m", "scm.mcp_server"]
    if args.http:
        cmd.extend(["--http", "--port", str(args.port)])
    import subprocess as _sp
    _sp.run(cmd, check=False)


def _mcp_status():
    """Check SCM MCP configuration status across all supported agents."""
    from . import mcp_setup as ms

    rows = ms.status_all()
    print("\nSCM MCP Status\n")
    configured = detected_total = 0
    for r in rows:
        det = r.get("detected", True)
        if det:
            detected_total += 1
        det_mark = "✓" if det else "·"
        if r["configured"]:
            label, icon = "configured", "✅"
            configured += 1
        elif r["exists"]:
            label, icon = "not configured", "○"
        else:
            label, icon = "not found", "·"
        print(f"   {det_mark} {icon} {r['display']:<20} {label}")
        print(f"        {r['path']}")
    print(f"\n{configured}/{detected_total} detected agents configured "
          f"({len(rows)} total supported).")
    if configured == 0:
        print("Run: scm mcp setup --all   (installs to detected agents only)")
    print()

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
from .reranker import SkillReranker
from .session import SessionTracker
from .optimizer import SkillOptimizer
from .feedback import FeedbackEngine
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
    p_index = sub.add_parser("index", help="Index skills from a directory")
    p_index.add_argument("--dir", type=str, default=".",
                         help="Directory containing skills (default: .)")
    p_index.add_argument("--recursive", action="store_true", default=True,
                         help="Scan subdirectories (default: true)")
    p_index.add_argument("--no-recursive", dest="recursive", action="store_false",
                         help="Don't scan subdirectories")

    # ── query ──
    p_query = sub.add_parser("query", help="Search for relevant skills")
    p_query.add_argument("query", nargs="*", help="Task description")
    p_query.add_argument("--query", dest="query_kw", type=str, help="Task description (alt)")
    p_query.add_argument("--top", type=int, default=5, help="Number of results (default: 5)")
    p_query.add_argument("--method", choices=["bm25", "embedding", "hybrid"],
                         default="hybrid", help="Search method (default: hybrid)")
    p_query.add_argument("--format", choices=["text", "json"], default="text",
                         help="Output format (default: text)")
    p_query.add_argument("--session", type=str, default="",
                         help="Session ID for session-aware boosting")
    p_query.add_argument("--rerank", action="store_true", default=True,
                         help="Use cross-encoder reranking (default: true)")
    p_query.add_argument("--no-rerank", dest="rerank", action="store_false",
                         help="Skip reranking")

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

    p_fb_stats = p_fb_sub.add_parser("stats", help="View feedback statistics")

    # ── stats ──
    sub.add_parser("stats", help="Show indexing statistics")

    # ── insights ──
    p_ins = sub.add_parser("insights", help="Show usage insights")
    p_ins.add_argument("--days", type=int, default=30, help="Days to analyze")

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
        "insights": cmd_insights,
    }

    commands[args.command](args)


# ── Commands ─────────────────────────────────────────────────────

def cmd_index(args):
    directory = Path(args.dir)
    print(f"📂 Scanning {directory} for skills...")

    indexer = SkillIndexer()
    count = indexer.index_directory(directory, recursive=args.recursive)
    stats = indexer.stats()

    print(f"✅ Indexed {count} skills")
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
    reranker = SkillReranker()

    start = time.time()

    if args.method == "bm25":
        results = retriever.bm25_search(query, top_k=args.top * 4)
    elif args.method == "embedding":
        results = retriever.embedding_search(query, top_k=args.top * 4)
    else:
        results = retriever.hybrid_search(query, top_k=args.top * 4)

    if args.session:
        session_tracker = SessionTracker()
        recent = session_tracker.get_recent_skills(args.session)
        if recent:
            results = retriever.apply_session_boost(results, recent, boost=0.5)

    feedback = FeedbackEngine()
    results = feedback.apply_weights(results)

    if args.rerank and len(results) > 1:
        results = reranker.rerank(query, results, top_k=args.top)

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
        sid = args.id or (tracker.get_active_session().session_id
                          if tracker.get_active_session() else "")
        if not sid:
            print("❌ No active session. Use 'scm session start --id <id>' first")
            return
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
        sid = args.id or (tracker.get_active_session().session_id
                          if tracker.get_active_session() else "")
        if not sid:
            print("❌ No session specified and no active session")
            return
        context = tracker.optimize_skill_context(sid, args.query)
        if args.query:
            retriever = SkillRetriever()
            results = retriever.hybrid_search(args.query, top_k=3)
            context["matching_skills"] = [
                {"name": r.skill.name, "description": r.skill.description}
                for r in results
            ]
        print(json.dumps(context, indent=2))
        print(f"\n📝 Add this to your agent's system prompt:")
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
        print(f"\n📊 Potential savings:")
        before = sum(r["before_tokens"] for r in changed)
        after = sum(r["after_tokens"] for r in changed)
        pct = ((before - after) / before * 100) if before else 0
        print(f"   Before: {before} meta tokens")
        print(f"   After:  {after} meta tokens")
        print(f"   Saved:  {before - after} tokens per load ({pct:.0f}%)")
        print(f"\n   Run with --no-dry-run to apply changes.\n")
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
        print(f"\n📊 Feedback Statistics")
        print(f"   Total feedback:    {stats['total_feedback']}")
        print(f"   Success rate:      {stats['success_rate']:.0%}")
        print(f"   Query patterns:    {stats['query_patterns']}")
        print(f"   Skills with data:  {stats['skills_with_feedback']}")
        if stats['top_skills']:
            print(f"\n   Top skills by success rate:")
            for s in stats['top_skills'][:5]:
                rate = s['rate']
                print(f"     • {s['name']}: {s['successes']}/{s['successes'] + s['failures']} "
                      f"({rate:.0%})")


def cmd_stats(args=None):
    indexer = SkillIndexer()
    stats = indexer.stats()

    print(f"\n📊 Skill Index Statistics")
    print(f"   Total skills:     {stats['total_skills']}")
    print(f"   Categories:       {len(stats['categories'])}")
    print(f"   Metadata tokens:  {stats['total_tokens_metadata']}")
    print(f"   Body tokens:      {stats['total_tokens_body']}")

    if stats['categories']:
        print(f"\n   By category:")
        for cat, count in sorted(stats['categories'].items(), key=lambda x: -x[1])[:10]:
            print(f"     • {cat}: {count} skills")

    feedback = FeedbackEngine()
    fb_stats = feedback.get_stats()
    if fb_stats['total_feedback'] > 0:
        print(f"\n   Feedback: {fb_stats['total_feedback']} records, "
              f"{fb_stats['success_rate']:.0%} success rate")


def cmd_insights(args):
    tracker = UsageTracker()
    insights = tracker.get_insights(days=args.days)

    print(f"\n📈 Usage Insights (last {insights['period_days']} days)")
    print(f"   Total queries:     {insights['total_queries']}")
    print(f"   Tokens saved:      ~{insights['tokens_saved_estimate']:,}")
    print(f"   Retrieval methods: {insights['retrieval_methods']}")

    if insights['top_skills']:
        print(f"\n   Top skills used:")
        for s in insights['top_skills'][:5]:
            print(f"     • {s['name']}: {s['count']} times")

    if insights['unused_skills']:
        count = len(insights['unused_skills'])
        sample = ', '.join(insights['unused_skills'][:5])
        print(f"\n   Unused skills ({count}): {sample}{'...' if count > 5 else ''}")

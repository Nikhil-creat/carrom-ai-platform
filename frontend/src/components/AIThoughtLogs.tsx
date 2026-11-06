import { useGame } from '../hooks/useWebSocket';

export default function AIThoughtLogs() {
  const thoughts = useGame((s) => s.thoughts);
  const latest = thoughts[thoughts.length - 1];
  if (!latest) {
    return <div className="rounded-lg bg-slate-800/60 p-3 text-sm text-slate-400">AI reasoning will appear here when the AI plays.</div>;
  }
  const d = latest.decision;
  return (
    <div className="space-y-2 rounded-lg bg-slate-800/60 p-3 text-sm">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-emerald-300">{d.agent_id}</span>
        <span className="rounded bg-slate-700 px-2 py-0.5 text-xs uppercase tracking-wide">{d.difficulty}</span>
      </div>
      <div><span className="text-slate-400">Strategy:</span> <b>{d.selected_strategy.replaceAll('_', ' ')}</b></div>
      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <div className="rounded bg-slate-900/60 p-1"><div className="text-slate-400">x</div>{d.shot_parameters.striker_x}</div>
        <div className="rounded bg-slate-900/60 p-1"><div className="text-slate-400">angle</div>{d.shot_parameters.aim_angle_deg}°</div>
        <div className="rounded bg-slate-900/60 p-1"><div className="text-slate-400">force</div>{d.shot_parameters.force_percentage}%</div>
      </div>
      <div className="text-xs text-slate-300">
        <div className="text-slate-400">RAG</div>
        {d.rag_retrieval.matching_historical_vector_id} · similarity {d.rag_retrieval.similarity_score}
        {d.rag_retrieval.rule_reference && <div>Rule: {d.rag_retrieval.rule_reference}</div>}
      </div>
      <ol className="list-decimal space-y-1 pl-5 text-xs text-slate-200">
        {d.thought_chain.map((t, i) => <li key={i}>{t}</li>)}
      </ol>
      <div className="text-xs text-slate-500">{d.simulations} simulations · expected value {d.expected_value}</div>
    </div>
  );
}

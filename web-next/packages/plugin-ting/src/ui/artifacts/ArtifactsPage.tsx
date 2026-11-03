import './Artifacts.css';
import { Link } from '@tanstack/react-router';
import { ArtifactList } from './ArtifactList';

/** Work › Artifacts: everything agents produced, and what waits on a person. */
export function ArtifactsPage() {
  return (
    <div className="ting-artifacts-page">
      <header className="ting-artifacts-page__head">
        <h1>Artifacts</h1>
        <p>What agents produced in your runs: plans, specs, reports and the decisions they wait on.</p>
        <Link to={'/ting/plan/new' as never}>Plan a project</Link>
      </header>
      <ArtifactList />
    </div>
  );
}

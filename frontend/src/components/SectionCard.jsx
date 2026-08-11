/** A titled panel. `actions` sits opposite the title in the header. */
export default function SectionCard({ id, title, subtitle, actions, children, bodyClass = "" }) {
  return (
    <section className="card" id={id}>
      {(title || actions) && (
        <header className="card-head">
          <div>
            {title ? <h3 className="card-title">{title}</h3> : null}
            {subtitle ? <p className="card-sub">{subtitle}</p> : null}
          </div>
          {actions ? <div className="row-2">{actions}</div> : null}
        </header>
      )}
      <div className={`card-body ${bodyClass}`}>{children}</div>
    </section>
  );
}

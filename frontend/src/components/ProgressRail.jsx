/** Pick progress. One segment per line, coloured by that line's real state. */
export default function ProgressRail({ items, currentIndex, onSecretTap }) {
  return (
    <div className="rail" onClick={onSecretTap} aria-label="Pick progress">
      {items.map((item, i) => (
        <span
          key={item.sku_id + i}
          data-state={item.state}
          data-current={i === currentIndex}
          title={`${item.name}: ${item.state}`}
        />
      ))}
    </div>
  );
}

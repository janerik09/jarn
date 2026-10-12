import './Pagination.css'

interface PaginationProps {
  currentPage: number
  totalItems: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
}

export function Pagination({
  currentPage,
  totalItems,
  pageSize,
  onPageChange,
  onPageSizeChange
}: PaginationProps) {
  const totalPages = Math.ceil(totalItems / pageSize)
  const startItem = (currentPage - 1) * pageSize + 1
  const endItem = Math.min(currentPage * pageSize, totalItems)

  const getPageNumbers = () => {
    const pages: (number | string)[] = []
    const maxVisible = 5

    if (totalPages <= maxVisible) {
      for (let i = 1; i <= totalPages; i++) {
        pages.push(i)
      }
    } else {
      pages.push(1)
      
      if (currentPage > 3) {
        pages.push('...')
      }

      const start = Math.max(2, currentPage - 1)
      const end = Math.min(totalPages - 1, currentPage + 1)

      for (let i = start; i <= end; i++) {
        pages.push(i)
      }

      if (currentPage < totalPages - 2) {
        pages.push('...')
      }

      pages.push(totalPages)
    }

    return pages
  }

  return (
    <div className="pagination-container">
      <div className="pagination-info">
        <span className="item-count">
          {totalItems === 0 ? 'No items' : `${startItem}–${endItem} of ${totalItems}`}
        </span>
        
        <div className="page-size-select">
          <label>Per page:</label>
          <select
            value={pageSize}
            onChange={(e) => {
              onPageSizeChange(parseInt(e.target.value))
              onPageChange(1)
            }}
          >
            <option value={10}>10</option>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </div>

      {totalPages > 1 && (
        <div className="pagination-controls">
          <button
            className="page-btn prev-btn"
            onClick={() => onPageChange(currentPage - 1)}
            disabled={currentPage === 1}
            title="Previous page"
          >
            ← Prev
          </button>

          <div className="page-numbers">
            {getPageNumbers().map((page, idx) => (
              <button
                key={idx}
                className={`page-number ${page === currentPage ? 'active' : ''} ${typeof page === 'string' ? 'ellipsis' : ''}`}
                onClick={() => typeof page === 'number' && onPageChange(page)}
                disabled={typeof page === 'string'}
              >
                {page}
              </button>
            ))}
          </div>

          <button
            className="page-btn next-btn"
            onClick={() => onPageChange(currentPage + 1)}
            disabled={currentPage === totalPages}
            title="Next page"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  )
}

interface NikeSwooshProps {
  className?: string
  color?: string
  width?: number
  height?: number
}

export default function NikeSwoosh({ className = '', color = '#FFFFFF', width = 80, height = 30 }: NikeSwooshProps) {
  return (
    <svg
      width={width}
      height={height}
      viewBox="0 0 80 30"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-label="Nike"
    >
      <path
        d="M8.027 22.911L24.234 3.493c.734-.874 1.836-1.38 3.009-1.38h42.49c.617 0 1.02.662.734 1.209L54.65 22.204c-.734 1.38-2.139 2.254-3.67 2.254H8.551c-.794 0-1.256-.92-.524-1.547z"
        fill={color}
      />
    </svg>
  )
}
